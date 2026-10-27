"""Actual role membership and object scope rows guard API/SQL business reads, including inheritance."""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, event, select

from app.common.auth import CurrentUser, resolve_user, row_filter
from app.common.row_filter import DENY_ALL
from app.common.sql_guard import run_guarded
from app.core import clock
from app.modules.admin.models import SysRole, SysRoleMember, SysRoleScope, SysUser
from app.modules.biz.models import BizOrder, OrderLine, OrderStatus, Product, Store, StoreStatus
from app.modules.ontology.models import OntologyRoleBinding
from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectType, Property

VIEWER = {"X-Demo-As": "U010006"}


@pytest.fixture
async def facts(users):
    user = await users.scalar(select(SysUser).where(SysUser.code == "U010006"))
    await users.execute(delete(SysRoleMember).where(SysRoleMember.user_id == user.id))
    for code, api, name in (
        ("order", "SalesOrder", "订单"),
        ("store", "Store", "门店"),
        ("product", "Product", "商品"),
    ):
        primary = {"order": "order_id", "store": "store_id", "product": "sku"}[code]
        users.add(
            ObjectType(
                code=code,
                api_name=api,
                display_name=name,
                obj_group="范围专项",
                conflict_strategy=ConflictStrategy.user_edit_wins,
                lifecycle=Lifecycle.active,
                pk_property=primary,
                properties=[Property(api_name=primary, display_name="编号", data_type="string", prop_group="标识",
                    source_column=primary, description="", is_pk=True, visibility="normal")],
            )
        )
    product = Product(
        code="scope_product",
        barcode="scope",
        name="测试商品",
        category="休闲食品/饼干",
        price=Decimal("1"),
        unit="件",
    )
    users.add(product)
    await users.flush()
    for n, region in enumerate(("华东", "华南", "华北")):
        store = Store(
            code=f"scope_store_{n}",
            store_no=f"SCOPE-{n}",
            name=f"真实{region}门店",
            city="上海",
            region=f"{region}一区",
            big_region=region,
            format="社区店",
            opened_at=date(2025, 1, 1),
            status=StoreStatus.open,
        )
        users.add(store)
        await users.flush()
        order = BizOrder(
            code=f"scope_order_{n}",
            store_id=store.id,
            channel="线上",
            status=OrderStatus.shipped,
            created_at_biz=datetime(2026, 10, 24, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
            amount=Decimal(n + 1),
        )
        users.add(order)
        await users.flush()
        users.add(
            OrderLine(
                order_id=order.id, line_no=1, product_id=product.id, qty=Decimal(n + 1), price=Decimal("1")
            )
        )
    await users.flush()
    return users, user


async def grant(facts, code, scopes=(), default_deny=False, view=True):
    session, user = facts
    role = SysRole(
        code=code,
        name=code,
        description="真实行权限专项",
        default_deny=default_deny,
        func={
            "metrics": "view",
            "object-types": "view",
            "object-search": "view",
            "object-detail": "view",
            "qa-workbench": "manage",
        },
        objs=[
            {"objType": name, "api": api, "view": view, "export": False, "actions": []}
            for api, name in (("SalesOrder", "订单"), ("Store", "门店"), ("Product", "商品"))
        ],
        changed_at=clock.now(),
        changed_by=user.name,
    )
    session.add(role)
    await session.flush()
    session.add(SysRoleMember(role_id=role.id, user_id=user.id, seq=0))
    session.add(OntologyRoleBinding(ontology_id=1, role_id=role.id))
    for n, (obj, prop, values) in enumerate(scopes):
        session.add(
            SysRoleScope(
                code=f"{code}-{n}", role_id=role.id, seq=n, obj_type=obj, prop=prop, op="∈", values=values
            )
        )
    await session.flush()
    return role


async def query(session, user, sql):
    return (await run_guarded(await session.connection(), sql, user)).scalar()


async def test_real_scope_api_sql_inheritance_and_membership_revocation(client, facts, engine):
    session, user = facts
    role = await grant(
        facts, "T-EAST", [("订单", "大区", ["华东"]), ("门店", "大区", ["华东"])], default_deny=True
    )
    response = await client.get("/api/biz/v1/orders", headers=VIEWER)
    assert response.status_code == 200 and response.json()["total"] == 1, response.text
    assert response.json()["items"][0]["id"] == "scope_order_0"
    assert (await client.get("/api/biz/v1/orders/scope_order_1", headers=VIEWER)).status_code == 404
    actor = await resolve_user(session, user.code)
    assert await query(session, actor, "select count(*) from biz_order") == 1
    assert await query(session, actor, "select sum(qty * price) from order_line") == Decimal("1")
    assert await query(session, actor, "select count(*) from product") == 0
    assert await query(session, actor, "select count(*) from store") == 1
    store_type = await session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    store_type.properties.append(
        Property(
            api_name="region_code",
            display_name="大区",
            data_type="enum",
            prop_group="范围",
            source_column="big_region",
            backing_column="big_region",
            filterable=True,
        )
    )
    await session.flush()
    facets = await client.get("/api/ontology/v1/object-types/store/facets", headers=VIEWER)
    assert facets.status_code == 200, facets.text
    assert facets.json()[0]["values"] == [{"value": "华东", "count": 1}]
    # Identity loads all scopes once; repeated use of the resolved identity makes no permission queries.
    statements = []

    def capture(_conn, _cursor, sql, *_):
        statements.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        for code in ("order", "store", "product", "unknown"):
            await row_filter(actor, code)
        assert statements == []
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    await session.execute(
        delete(SysRoleMember).where(SysRoleMember.role_id == role.id, SysRoleMember.user_id == user.id)
    )
    response = await client.get("/api/biz/v1/orders", headers=VIEWER)
    assert response.status_code == 200 and response.json()["total"] == 0
    assert (
        await row_filter(CurrentUser(id="U010001", name="王敏", is_ontology_admin=True), "order") == DENY_ALL
    )


async def test_real_roles_or_unlimited_default_deny_and_invalid_mapping(facts):
    session, user = facts
    east = await grant(facts, "T-EAST", [("订单", "大区", ["华东"])])
    await grant(facts, "T-SOUTH", [("订单", "大区", ["华南"])])
    assert await query(session, await resolve_user(session, user.code), "select count(*) from biz_order") == 2
    unrestricted = await grant(facts, "T-ALL")
    assert await row_filter(await resolve_user(session, user.code), "order") is None
    unrestricted.default_deny = True
    await session.flush()
    assert await query(session, await resolve_user(session, user.code), "select count(*) from biz_order") == 2
    await session.execute(delete(SysRoleMember).where(SysRoleMember.user_id == user.id))
    invalid = await grant(facts, "T-INVALID", [("订单", "不存在的权限依据", ["华东' OR 1=1"])])
    assert await query(session, await resolve_user(session, user.code), "select count(*) from biz_order") == 0
    scope = await session.scalar(select(SysRoleScope).where(SysRoleScope.role_id == invalid.id))
    scope.obj_type = "错误的对象名称"
    await session.flush()
    assert await query(session, await resolve_user(session, user.code), "select count(*) from biz_order") == 0
    invalid.objs = [{"objType": "订单", "api": "wrong_api", "view": True}]
    await session.flush()
    assert await row_filter(await resolve_user(session, user.code), "order") == DENY_ALL
    # Stable API identity survives a display rename; scopes retain the role's recorded object label.
    await session.execute(delete(SysRoleMember).where(SysRoleMember.user_id == user.id))
    session.add(SysRoleMember(role_id=east.id, user_id=user.id, seq=0))
    order = await session.scalar(select(ObjectType).where(ObjectType.code == "order"))
    order.display_name = "重命名订单"
    await session.flush()
    assert await query(session, await resolve_user(session, user.code), "select count(*) from biz_order") == 1


async def test_real_category_alias_and_exact_store_name_scope(facts):
    session, user = facts
    role = await grant(
        facts,
        "T-CATEGORY",
        [("商品", "品类", ["休闲零食"]), ("订单", "门店", ["真实华南门店"])],
        default_deny=True,
    )
    actor = await resolve_user(session, user.code)
    assert await query(session, actor, "select count(*) from product") == 1
    assert await query(session, actor, "select count(*) from biz_order") == 1
    scope = await session.scalar(
        select(SysRoleScope).where(SysRoleScope.role_id == role.id, SysRoleScope.obj_type == "订单")
    )
    scope.values = ["华南门店"]
    await session.flush()
    assert await query(session, await resolve_user(session, user.code), "select count(*) from biz_order") == 0
