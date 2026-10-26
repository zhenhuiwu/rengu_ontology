"""角色与权限（admin，前端 src/pages/admin/Roles.vue，api-index §4.2）。

对照方法：export-mock 导出的 ROLES.json / derived/roleMembers.json 是种子和期望；有效权限的期望
tests/fixtures/roles_effective_access.json 是用 Vite 加载 src/mock/roles.ts，对 120 个用户逐个调用
effectiveAccess() 得到的（func 只留 [key, lv]；生成脚本见 docs/backend/admin-roles-api.md）。
"""

import json

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.admin.models import AuditLog, SysRole, SysRoleMember, SysUser, UserStatus

SYS = get_settings().seeds_json_dir / "system"
ROLES = json.loads((SYS / "ROLES.json").read_text("utf-8"))
MEMBERS = {
    c["args"][0]["id"]: [u["id"] for u in c["result"]]
    for c in json.loads((SYS / "derived" / "roleMembers.json").read_text("utf-8"))
}
EFF = json.loads(
    (get_settings().seeds_json_dir.parent.parent / "tests/fixtures/roles_effective_access.json").read_text(
        "utf-8"
    )
)
URL = "/api/admin/v1/roles"


@pytest.fixture(autouse=True)
async def role_actor(client, users):
    # Use a real enabled seeded role member without changing the member/count compatibility fixtures.
    actor = await users.scalar(
        select(SysUser.code)
        .join(SysRoleMember, SysRoleMember.user_id == SysUser.id)
        .join(SysRole, SysRole.id == SysRoleMember.role_id)
        .where(SysRole.code == "R-01", SysUser.status == UserStatus.active)
        .order_by(SysUser.code.desc())
        .limit(1)
    )
    assert actor is not None
    client.headers["X-Demo-As"] = actor


def role_fields(r):
    return {
        k: r[k] for k in ("id", "name", "desc", "builtIn", "updatedAt", "updatedBy", "func", "objs", "scopes")
    } | {"defaultDeny": bool(r.get("defaultDeny"))}


async def test_list_and_get_match_mock(client, users):
    r = await client.get(URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [role_fields(x) for x in body] == [role_fields(x) for x in ROLES]
    assert [x["memberCount"] for x in body] == [len(MEMBERS[x["id"]]) for x in ROLES]
    one = (await client.get(f"{URL}/R-04")).json()
    assert role_fields(one) == role_fields(ROLES[3])
    assert (await client.get(f"{URL}/R-99")).status_code == 404


async def test_meta(client, users):
    m = (await client.get(f"{URL}/meta")).json()
    assert m["funcPages"] == json.loads((SYS / "FUNC_PAGES.json").read_text("utf-8"))
    assert m["permObjects"] == json.loads((SYS / "PERM_OBJECTS.json").read_text("utf-8"))
    assert [{k: p[k] for k in ("objType", "prop", "values")} for p in m["scopeProps"]] == json.loads(
        (SYS / "SCOPE_PROPS.json").read_text("utf-8")
    )
    assert m["objActions"]["门店"] == ["变更门店状态"]


@pytest.mark.parametrize("rid", ["R-01", "R-05", "R-06"])
async def test_members_order_and_paging(client, users, rid):
    exp = MEMBERS[rid]
    got = []
    for page in range(1, -(-len(exp) // 15) + 1):
        b = (await client.get(f"{URL}/{rid}/members", params={"page": page, "size": 15})).json()
        assert b["total"] == len(exp)
        got += [x["id"] for x in b["items"]]
    assert got == exp


async def test_members_kw(client, users):
    b = (await client.get(f"{URL}/R-05/members", params={"kw": "U0102", "size": 100})).json()
    assert [x["id"] for x in b["items"]] == [u for u in MEMBERS["R-05"] if "U0102" in u]


async def test_effective_access_matches_mock_for_all_users(client, users):
    for e in EFF:
        r = await client.get(f"/api/admin/v1/users/{e['id']}/effective-access")
        assert r.status_code == 200, r.text
        b = r.json()
        assert [x["id"] for x in b["rs"]] == e["rs"], e["id"]
        assert [[x["key"], x["lv"]] for x in b["func"]] == e["func"], e["id"]
        assert b["objs"] == e["objs"], e["id"]
        assert b["areasIn"] == e["areasIn"], e["id"]


async def test_built_in_is_readonly(client, users):
    assert (await client.patch(f"{URL}/R-01", json={"name": "x", "desc": ""})).status_code == 403
    assert (await client.put(f"{URL}/R-01/permissions", json={"func": {}, "objs": []})).status_code == 403
    assert (await client.delete(f"{URL}/R-01")).status_code == 403
    sc = {"objType": "订单", "prop": "大区", "values": ["华东"]}
    assert (await client.post(f"{URL}/R-03/scopes", json=sc)).status_code == 403
    # 成员可以改
    assert (await client.delete(f"{URL}/R-01/members/{MEMBERS['R-01'][0]}")).status_code == 204


async def test_edit_permissions_scope_members_flow(client, users):
    s = users
    before = await s.scalar(select(func.count()).select_from(AuditLog))
    r4 = ROLES[3]
    func_ = dict(r4["func"]) | {"metrics": "view"}
    objs = [dict(o) for o in r4["objs"]]
    objs[0] = objs[0] | {"view": False, "export": True}  # 取消查看 → 导出、动作清空
    r = await client.put(f"{URL}/R-04/permissions", json={"func": func_, "objs": objs})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["func"]["metrics"] == "view" and b["objs"][0] == {
        **objs[0],
        "view": False,
        "export": False,
        "actions": [],
    }
    assert b["updatedBy"]
    bad = [dict(o) for o in r4["objs"]]
    bad[0]["actions"] = ["关闭告警"]
    assert (await client.put(f"{URL}/R-04/permissions", json={"func": func_, "objs": bad})).status_code == 422
    # 范围：必须是权限依据，op 按取值个数推
    ok = await client.post(
        f"{URL}/R-04/scopes", json={"objType": "门店", "prop": "大区", "values": ["华东", "华南"]}
    )
    assert ok.status_code == 200 and ok.json()["op"] == "∈" and ok.json()["id"] == "S-84"
    no = await client.post(f"{URL}/R-04/scopes", json={"objType": "门店", "prop": "门店", "values": ["x"]})
    assert no.status_code == 422
    assert (await client.delete(f"{URL}/R-04/scopes/S-41")).status_code == 204
    got = (await client.get(f"{URL}/R-04")).json()
    assert [x["id"] for x in got["scopes"]] == ["S-42", "S-84"]
    # 成员：新加的排最前，已是成员跳过
    newbies = [u for u in MEMBERS["R-01"] if u not in MEMBERS["R-04"]][:2]
    r = await client.post(f"{URL}/R-04/members", json={"userIds": [*newbies, MEMBERS["R-04"][0]]})
    assert r.json() == {"added": 2}
    b = (await client.get(f"{URL}/R-04/members", params={"size": 100})).json()
    assert [x["id"] for x in b["items"]] == newbies + MEMBERS["R-04"]
    assert (await client.delete(f"{URL}/R-04/members/{newbies[0]}")).status_code == 204
    assert (await client.delete(f"{URL}/R-04/members/{newbies[0]}")).status_code == 404
    after = await s.scalar(select(func.count()).select_from(AuditLog))
    assert after - before == 5  # 权限、加范围、删范围、加成员、移出（422 不写）


async def test_create_copy_rename_delete(client, users):
    r = await client.post(URL, json={"name": "测试角色", "desc": "d", "perms": ["查看对象", "导出数据"]})
    assert r.status_code == 200, r.text
    n = r.json()
    assert n["id"] == "R-09" and not n["builtIn"] and n["memberCount"] == 0
    assert all(o["view"] and o["export"] and o["actions"] == [] for o in n["objs"])
    assert n["func"]["object-search"] == "view" and n["func"]["users"] == "none"
    assert (await client.post(URL, json={"name": "测试角色"})).status_code == 409
    assert (await client.post(URL, json={"name": "x2", "perms": ["乱写"]})).status_code == 422
    c = (await client.post(URL, json={"name": "店长副本", "copyFrom": "R-06"})).json()
    assert c["func"] == ROLES[5]["func"] and c["memberCount"] == 0 and c["defaultDeny"]
    assert [(x["objType"], x["values"]) for x in c["scopes"]] == [
        (x["objType"], x["values"]) for x in ROLES[5]["scopes"]
    ]
    # 改名同步用户页的主角色
    u = await client.patch(f"{URL}/R-06", json={"name": "店长", "desc": "x"})
    assert u.status_code == 200 and u.json()["name"] == "店长"
    users_ = (await client.get("/api/admin/v1/users", params={"size": 200})).json()["items"]
    assert "门店店长" not in {x["role"] for x in users_}
    assert (await client.patch(f"{URL}/R-07", json={"name": "店长"})).status_code == 409
    assert (await client.delete(f"{URL}/R-06")).status_code == 204
    assert (await client.get(f"{URL}/R-06")).status_code == 404
    assert len((await client.get(URL)).json()) == 9


async def test_users_role_check_reads_table(client, users):
    await client.post(URL, json={"name": "新角色甲"})
    body = {"name": "甲", "account": "jia@freshmart.cn", "dept": "华东事业部 / 运营中心", "role": "新角色甲"}
    assert (await client.post("/api/admin/v1/users", json=body)).status_code == 200
    assert (
        await client.post("/api/admin/v1/users", json=body | {"account": "y@x", "role": "无此角色"})
    ).status_code == 422


async def test_my_row_scope_matches_effective_access(client, users):
    """/me/row-scope 和角色页有效权限预览口径一致（同一 fixture）。"""
    checked = 0
    for e in EFF[:60]:
        r = await client.get("/api/admin/v1/me/row-scope", headers={"X-Demo-As": e["id"]})
        if r.status_code == 401:  # 停用 / 待激活用户登录不了
            continue
        assert r.status_code == 200, r.text
        checked += 1
        b = r.json()
        assert b["userId"] == e["id"]
        vis = {x["objType"]: x for x in b["items"] if x["visible"]}
        assert {k: v["text"] for k, v in vis.items()} == {o["name"]: o["scope"] for o in e["objs"]}, e["id"]
        for x in vis.values():
            assert x["unrestricted"] == (x["text"] == "全部")
            assert x["unrestricted"] or x["conditions"]
            assert all(c["prop"] and c["values"] for c in x["conditions"])
        for x in b["items"]:
            if not x["visible"]:
                assert x["text"] == "无权查看" and not x["conditions"]
    assert checked >= 20


async def test_tenants_from_db(client, users):
    r = await client.get("/api/admin/v1/tenants")
    assert r.status_code == 200, r.text
    b = r.json()
    assert [x["value"] for x in b] == ["hd", "hn", "hq"]
    assert b[0]["label"] == "鲜邻零售集团 / 华东事业部" and b[0]["default"]
    assert b[0]["tenantId"] == "TN-XL-000317"
    assert sum(x["default"] for x in b) == 1


async def test_delete_role_in_use_and_primary_reassign(client, users):
    """删角色：有人只有这个角色 → 409 ROLE_IN_USE；都还有别的角色 → 删除，主角色改成剩下编号最小的角色。"""
    assert (await client.post(URL, json={"name": "新角色乙"})).json()["id"] == "R-09"
    body = {
        "name": "乙",
        "account": "yi@freshmart.cn",
        "dept": "华东事业部 / 运营中心",
        "role": "新角色乙",
        "status": "启用",
    }
    u = (await client.post("/api/admin/v1/users", json=body)).json()
    assert (await client.post(f"{URL}/R-09/members", json={"userIds": [u["id"]]})).status_code == 200
    r = await client.delete(f"{URL}/R-09")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "ROLE_IN_USE"
    assert r.json()["details"] == {"count": 1, "users": [{"id": u["id"], "name": "乙"}]}
    assert (await client.get(f"{URL}/R-09")).status_code == 200  # 没删
    assert (await client.post(f"{URL}/R-08/members", json={"userIds": [u["id"]]})).status_code == 200
    assert (await client.post(f"{URL}/R-07/members", json={"userIds": [u["id"]]})).status_code == 200
    r7 = (await client.get(f"{URL}/R-07")).json()["name"]
    assert (await client.delete(f"{URL}/R-09")).status_code == 204
    items = (await client.get("/api/admin/v1/users", params={"size": 200, "kw": "yi@freshmart.cn"})).json()[
        "items"
    ]
    assert [x["role"] for x in items if x["id"] == u["id"]] == [r7]


async def test_perm_objects_read_ontology(client, users, session):
    """权限对象读本体对象类型（已发布，按 seq）；兜底清单里本体没有的接在后面；新对象能保存权限。"""
    from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectStatus, ObjectType

    def ot(code, api, name, seq, status=ObjectStatus.published):
        return ObjectType(
            code=f"t-{code}",
            api_name=api,
            display_name=name,
            obj_group="交易",
            status=status,
            seq=seq,
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=Lifecycle.active,
        )

    session.add_all(
        [
            ot("o", "SalesOrder", "订单", 0),
            ot("s", "Supplier", "供应商", 1),
            ot("d", "DraftX", "草稿对象", 2, ObjectStatus.draft),
        ]
    )
    await session.flush()
    fallback = json.loads((SYS / "PERM_OBJECTS.json").read_text("utf-8"))
    m = (await client.get(f"{URL}/meta")).json()
    names = [o["name"] for o in m["permObjects"]]
    assert names[:2] == ["订单", "供应商"] and "草稿对象" not in names
    assert names[2:] == [o["name"] for o in fallback if o["name"] != "订单"]
    assert m["objActions"]["订单"][: len(fallback[0]["actions"])] == fallback[0]["actions"]
    assert m["objActions"]["供应商"] == []
    obj = {"objType": "供应商", "api": "Supplier", "view": True, "export": False, "actions": []}
    r = await client.put(f"{URL}/R-04/permissions", json={"func": ROLES[3]["func"], "objs": [obj]})
    assert r.status_code == 200, r.text
    assert [o for o in r.json()["objs"] if o["objType"] == "供应商"] == [obj]


@pytest.mark.parametrize("data_type", ["string", "enum"])
async def test_scope_props_follow_filter_flags_and_executable_basis(
    client, users, session, monkeypatch, data_type
):
    """本体属性、执行白名单、可见取值同源；不支持的类型/隐藏/敏感/未登记字段不能保存。"""
    from datetime import date

    from app.common import auth
    from app.modules.biz.models import Store, StoreStatus
    from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectStatus, ObjectType, Property

    obj = ObjectType(
        code="store",
        api_name="Store",
        display_name="门店",
        obj_group="组织",
        status=ObjectStatus.published,
        seq=0,
        conflict_strategy=ConflictStrategy.user_edit_wins,
        lifecycle=Lifecycle.active,
    )
    session.add(obj)
    await session.flush()
    prop = Property(
        object_type_id=obj.id,
        api_name="area",
        display_name="所属区域",
        data_type=data_type,
        prop_group="组织",
        source_column="region",
        backing_column="region",
        filterable=True,
    )
    session.add(prop)
    for i, flags in enumerate(
        [{"data_type": typ} for typ in ("integer", "decimal", "date", "timestamp", "boolean", "array")]
        + [{"visibility": "hidden"}, {"is_sensitive": True}, {"filterable": False}, {"backing_column": None}]
    ):
        session.add(
            Property(
                **{
                    "object_type_id": obj.id,
                    "api_name": f"excluded_{i}",
                    "display_name": f"排除属性{i}",
                    "data_type": "string",
                    "prop_group": "组织",
                    "source_column": "region",
                    "backing_column": "region",
                    "filterable": True,
                    **flags,
                }
            )
        )
    for i, region in enumerate(["允许区", "不可见区"]):
        session.add(
            Store(
                code=f"scope-store-{i}",
                store_no=f"scope-store-{i}",
                name=f"范围门店{i}",
                city="测试市",
                region=region,
                big_region="华东",
                format="社区店",
                opened_at=date(2026, 1, 1),
                status=StoreStatus.open,
            )
        )
    await session.flush()

    async def restricted(user, object_type):
        return {"field": "region", "op": "=", "value": "允许区"}

    monkeypatch.setattr(auth, "row_filter", restricted)
    response = await client.get(f"{URL}/meta")
    assert response.status_code == 200, response.text
    assert response.json()["scopeProps"] == [
        {
            "objType": "门店",
            "prop": "区域",
            "displayName": "所属区域",
            "dataType": data_type,
            "values": ["允许区"],
        }
    ]
    body = {"objType": "门店", "prop": "区域", "values": ["允许区"]}
    ok = await client.post(f"{URL}/R-04/scopes", json=body)
    assert ok.status_code == 200, ok.text
    assert (await client.post(f"{URL}/R-04/scopes", json={**body, "values": ["不可见区"]})).status_code == 422
    assert (await client.post(f"{URL}/R-04/scopes", json={**body, "prop": "排除属性0"})).status_code == 422

    async def denied(user, object_type):
        return {"deny": True}

    monkeypatch.setattr(auth, "row_filter", denied)
    assert (await client.get(f"{URL}/meta")).json()["scopeProps"][0]["values"] == []
    assert (await client.post(f"{URL}/R-04/scopes", json=body)).status_code == 422
    monkeypatch.setattr(auth, "row_filter", restricted)
    prop.filterable = False
    await session.flush()
    assert (await client.get(f"{URL}/meta")).json()["scopeProps"] == []  # 不拿旧 JSON 填回已移除的属性
    assert (await client.post(f"{URL}/R-04/scopes", json=body)).status_code == 422


async def test_scope_props_use_registered_link_values_and_propagate_errors(
    client, users, session, monkeypatch
):
    from app.common import auth
    from app.core.errors import AppError
    from app.modules.biz.models import BizOrder, Store
    from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectStatus, ObjectType, Property
    from app.seeds.loader import run_seeds

    await run_seeds(session, ["biz"])
    obj = ObjectType(
        code="order",
        api_name="SalesOrder",
        display_name="订单",
        obj_group="交易",
        status=ObjectStatus.published,
        seq=0,
        conflict_strategy=ConflictStrategy.user_edit_wins,
        lifecycle=Lifecycle.active,
    )
    session.add(obj)
    await session.flush()
    session.add(
        Property(
            object_type_id=obj.id,
            api_name="store_id",
            display_name="门店",
            data_type="string",
            prop_group="交易",
            source_column="store_id",
            filterable=True,
        )
    )
    await session.flush()
    expected = sorted(set((await session.scalars(select(Store.name).join(BizOrder))).all()))
    response = await client.get(f"{URL}/meta")
    assert response.status_code == 200, response.text
    assert response.json()["scopeProps"][0]["values"] == expected
    assert expected

    region = await session.scalar(select(Store.region).join(BizOrder).limit(1))

    async def scoped_orders(user, object_type):
        if object_type == "order":
            return {"field": "order_belongs_to_store.region", "op": "=", "value": region}
        return None

    monkeypatch.setattr(auth, "row_filter", scoped_orders)
    restricted = sorted(
        set((await session.scalars(select(Store.name).join(BizOrder).where(Store.region == region))).all())
    )
    response = await client.get(f"{URL}/meta")
    assert response.status_code == 200, response.text
    assert response.json()["scopeProps"][0]["values"] == restricted
    assert restricted and set(restricted) < set(expected)

    async def bad_filter(user, object_type):
        raise AppError("筛选属性已失效", code="BAD_ROW_FILTER", status_code=422)

    monkeypatch.setattr(auth, "row_filter", bad_filter)
    response = await client.get(f"{URL}/meta")
    assert response.status_code == 422
    assert response.json()["code"] == "BAD_ROW_FILTER"


async def test_scope_props_reject_real_values_that_legacy_aliases_would_rewrite(client, users, session):
    from app.common.sql_guard import scope_expr
    from app.modules.biz.governance import PRODUCT_GOV
    from app.modules.biz.models import Product
    from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectStatus, ObjectType, Property

    obj = ObjectType(
        code="product",
        api_name="Product",
        display_name="商品",
        obj_group="商品",
        status=ObjectStatus.published,
        conflict_strategy=ConflictStrategy.user_edit_wins,
        lifecycle=Lifecycle.active,
    )
    session.add(obj)
    await session.flush()
    session.add(
        Property(
            object_type_id=obj.id,
            api_name="category",
            display_name="品类",
            data_type="string",
            prop_group="商品",
            source_column="category",
            filterable=True,
        )
    )
    for n, category in enumerate(["休闲零食/自定义", "休闲食品/标准"]):
        session.add(
            Product(
                code=f"alias-{n}", barcode=f"alias-{n}", name=category, category=category, price=1, unit="个"
            )
        )
    await session.flush()
    response = await client.get(f"{URL}/meta")
    assert response.status_code == 200, response.text
    assert response.json()["scopeProps"][0]["values"] == ["休闲食品"]
    body = {"objType": "商品", "prop": "品类", "values": ["休闲零食"]}
    response = await client.post(f"{URL}/R-04/scopes", json=body)
    assert response.status_code == 422, response.text
    assert (await client.post(f"{URL}/R-04/scopes", json={**body, "values": ["休闲食品"]})).status_code == 200
    # 既有角色的演示别名仍按原契约执行，不把旧配置改成新含义。
    assert scope_expr(PRODUCT_GOV, [{"prop": "品类", "op": "=", "values": ["休闲零食"]}]) == {
        "field": "category_l1",
        "op": "=",
        "value": "休闲食品",
    }
