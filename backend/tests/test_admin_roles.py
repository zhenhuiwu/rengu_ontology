"""角色与权限（admin，前端 src/pages/admin/Roles.vue，api-index §4.2）。

对照方法：export-mock 导出的 ROLES.json / derived/roleMembers.json 是种子和期望；有效权限的期望
tests/fixtures/roles_effective_access.json 是用 Vite 加载 src/mock/roles.ts，对 120 个用户逐个调用
effectiveAccess() 得到的（func 只留 [key, lv]；生成脚本见 docs/backend/admin-roles-api.md）。
"""

import json

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.admin.models import AuditLog

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
    assert m["scopeProps"] == json.loads((SYS / "SCOPE_PROPS.json").read_text("utf-8"))
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
