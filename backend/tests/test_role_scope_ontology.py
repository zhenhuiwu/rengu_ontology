"""admin_0025: sys_role_scope is per ontology and references object types by id."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import resolve_user
from app.common.ontology import set_ontology_scope
from app.modules.admin.models import SysRoleScope
from app.modules.ontology.models import OntologyWorkspace
from app.modules.static.models import ObjectStatus, ObjectType, Property
from tests import test_object_row_access as row_access

ADMIN = {"X-Demo-As": "U010245"}
facts = row_access.facts  # shared fixture
grant, query = row_access.grant, row_access.query


async def count_orders(session, user):
    return await query(session, await resolve_user(session, user.code), "select count(*) from biz_order")


async def test_object_type_id_reference_wins_over_label_and_deleted_reference_denies(facts):
    session, user = facts
    role = await grant(facts, "T-ID", [("订单", "大区", ["华东"])], default_deny=True)
    scope = await session.scalar(select(SysRoleScope).where(SysRoleScope.role_id == role.id))
    order = await session.scalar(select(ObjectType).where(ObjectType.code == "order"))
    assert scope.ontology_id == 1
    scope.object_type_id, scope.object_type_code = order.id, "order"
    scope.obj_type = "已失效的旧标签"  # label no longer used once the id reference exists
    await session.flush()
    assert await count_orders(session, user) == 1
    # Referenced object type removed (FK ON DELETE SET NULL keeps the code): unmapped → deny all.
    scope.object_type_id = None
    await session.flush()
    assert await count_orders(session, user) == 0


async def test_scopes_of_other_ontology_do_not_apply(facts, conn):
    session, user = facts
    role = await grant(facts, "T-NOSCOPE", default_deny=False)
    assert await count_orders(session, user) == 3
    workspace = OntologyWorkspace(code="scope-second", name="scope second")
    session.add(workspace)
    await session.flush()
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as other:
        await set_ontology_scope(other, workspace.id)
        other.add(
            SysRoleScope(
                code="T-NOSCOPE-x",
                role_id=role.id,
                seq=0,
                obj_type="订单",
                prop="大区",
                op="∈",
                values=["无"],
            )
        )
        await other.flush()
        assert await other.scalar(
            select(SysRoleScope.ontology_id).where(SysRoleScope.code == "T-NOSCOPE-x")
        ) == (workspace.id)
        await other.commit()
    await set_ontology_scope(session, 1)
    # The ontology-2 restriction is invisible in ontology 1: the role stays unrestricted here.
    assert await count_orders(session, user) == 3


async def test_add_scope_api_maps_object_type_code(client, facts):
    session, _user = facts
    role = await grant(facts, "T-API", default_deny=True)
    store = await session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    store.status = ObjectStatus.published
    store.properties.append(
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
    response = await client.post(
        f"/api/admin/v1/roles/{role.code}/scopes",
        json={"objType": "旧名称", "objectTypeCode": "store", "prop": "大区", "op": "∈", "values": ["华东"]},
        headers=ADMIN,
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["objectTypeCode"] == "store" and response.json()["objType"] == "门店"
    stored = await session.scalar(select(SysRoleScope).where(SysRoleScope.role_id == role.id))
    assert (stored.object_type_id, stored.ontology_id) == (store.id, 1)
    bad = await client.post(
        f"/api/admin/v1/roles/{role.code}/scopes",
        json={"objType": "门店", "objectTypeCode": "nope", "prop": "大区", "op": "∈", "values": ["华东"]},
        headers=ADMIN,
    )
    assert bad.status_code == 422
