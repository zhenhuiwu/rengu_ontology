"""用户与组织（admin，前端 src/pages/admin/Users.vue，api-index §4.1）。

对照方法：用 export-mock 导出的 ADMIN_USERS.json 在 Python 里按 Users.vue 的 rows 过滤 + paginate() 算出期望，
逐页逐字段比对；待审批计数只读真实记录，无记录为0。
"""

import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.admin.models import SysOrg, SysUser, UserStatus
from tests.admin_committed_support import committed_approval_rows  # noqa: F401

MOCK = json.loads((get_settings().seeds_json_dir / "system" / "ADMIN_USERS.json").read_text("utf-8"))
URL = "/api/admin/v1/users"
DEPTS = [
    "华东事业部 / 运营中心",
    "华东事业部 / 财务数据组",
    "华东事业部 / 供应链",
    "华东事业部 / 会员中心",
    "集团总部 / 数据平台",
    "集团总部 / 信息安全",
    "华南事业部 / 运营中心",
]


@pytest.fixture(autouse=True)
async def permission_roles(users):
    from tests.function_access_support import grant_fixture_roles
    from tests.helpers.workspace import bind_workspace_members

    await grant_fixture_roles(users)
    await bind_workspace_members(users)  # 测试会话预绑定空间 1：操作人须是空间成员


def pending(u):
    """这些测试只seed用户，没有待审批业务记录。"""
    return 0


def expect(dept="全部", status="全部", kw=""):
    """Users.vue 的 rows computed。"""
    return [
        {**u, "pendingApprovals": pending(u)}
        for u in MOCK
        if (dept == "全部" or u["dept"] == dept)
        and (status == "全部" or u["status"] == status)
        and (not kw or kw in u["name"] or kw in u["account"] or kw in u["id"])
    ]


def mock_page(rows, page, size):
    """src/mock/paginate.ts paginate()。"""
    pages = max(1, -(-len(rows) // size))
    p = min(max(1, page), pages)
    return rows[(p - 1) * size : p * size]


FIELDS = ("id", "name", "account", "dept", "role", "status", "lastLogin", "createdAt", "pendingApprovals")


def pick(r):
    return {k: r[k] for k in FIELDS}


def test_mock_export_shape():
    assert len(MOCK) == 120 and len({u["id"] for u in MOCK}) == 120
    assert [u["id"] for u in MOCK] == sorted(u["id"] for u in MOCK)  # mock 顺序 = 编号升序
    assert {u["dept"] for u in MOCK} <= set(DEPTS)


async def test_seed_backfills_role_and_created(users):
    rows = {u.code: u for u in (await users.scalars(select(SysUser))).all()}
    for m in MOCK:
        u = rows[m["id"]]
        assert u.role == m["role"] and u.joined_on.isoformat() == m["createdAt"]
    assert rows["U010001"].role is None  # SYS_USERS 占位人


@pytest.mark.parametrize(
    "flt",
    [
        {},
        {"dept": "全部", "status": "全部"},
        {"dept": "集团总部 / 数据平台"},
        {"status": "停用"},
        {"status": "待激活"},
        {"status": "启用", "dept": "华东事业部 / 运营中心"},
        {"kw": "王"},
        {"kw": "wang."},
        {"kw": "U0102"},
        {"kw": "xianlin"},
        {"kw": "不存在的人"},
    ],
)
async def test_list_matches_mock_all_pages(client, users, flt):
    exp = expect(**flt)
    pages = max(1, -(-len(exp) // 17))
    for page in range(1, pages + 2):  # 多翻一页：越界收拢到最后一页
        r = await client.get(URL, params={**flt, "page": page, "size": 17})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == len(exp)
        assert body["page"] == min(page, pages)
        assert [pick(x) for x in body["items"]] == [pick(x) for x in mock_page(exp, page, 17)]


async def test_list_defaults_and_page_zero(client, users):
    r = (await client.get(URL, params={"page": 0})).json()
    assert r["page"] == 1 and r["size"] == 20 and len(r["items"]) == 20 and r["total"] == 120
    assert r["items"][0]["id"] == "U010231" and "U010001" not in {x["id"] for x in r["items"]}
    assert (await client.get(URL, params={"status": "删除"})).status_code == 422


async def test_depts(client, users):
    r = await client.get("/api/admin/v1/depts")
    assert r.status_code == 200
    orgs = list(await users.scalars(select(SysOrg.name).order_by(SysOrg.seq, SysOrg.id)))
    assert r.json()[: len(orgs)] == orgs
    assert set(r.json()) == set(DEPTS) | set(orgs) and len(r.json()) == len(set(r.json()))


NEW = {
    "name": "陈思远",
    "account": "chen.siyuan@freshmart.cn",
    "dept": DEPTS[0],
    "role": "业务查看",
    "status": "待激活",
}


async def test_create_user(client, users):
    r = await client.post(URL, json=NEW)
    assert r.status_code == 200, r.text
    u = r.json()
    assert (
        u["id"] == "U011065"
        and u["status"] == "待激活"
        and u["lastLogin"] is None
        and u["pendingApprovals"] == 0
    )
    assert u["createdAt"] and u["role"] == "业务查看"
    body = (await client.get(URL, params={"kw": "chen.siyuan"})).json()
    assert body["total"] == 1 and body["items"][0]["id"] == u["id"]
    # 账号唯一
    r = await client.post(URL, json=NEW)
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_KEY"


@pytest.mark.parametrize(
    "patch",
    [{"account": "not-an-email"}, {"role": "不存在的角色"}, {"name": ""}, {"status": "删除"}],
)
async def test_create_validation(client, users, patch):
    r = await client.post(URL, json={**NEW, **patch})
    assert r.status_code == 422, r.text


async def test_update_user(client, users):
    m = MOCK[3]
    body = {"name": "改名", "dept": DEPTS[5], "role": "数据分析师", "status": "停用"}
    r = await client.put(f"{URL}/{m['id']}", json=body)
    assert r.status_code == 200, r.text
    u = r.json()
    assert u["account"] == m["account"] and u["dept"] == DEPTS[5] and u["status"] == "停用"
    assert u["pendingApprovals"] == 0
    assert (await client.put(f"{URL}/U999999", json=body)).status_code == 404
    assert (await client.put(f"{URL}/U010001", json=body)).status_code == 404  # 占位人不在列表里
    assert (await client.put(f"{URL}/{m['id']}", json={**body, "role": "x"})).status_code == 422


async def test_update_user_version_conflict_preserves_saved_values(client, users):
    code = MOCK[3]["id"]
    before = (await client.get(URL, params={"kw": code})).json()["items"][0]
    version = before["lockVersion"]
    body = {k: before[k] for k in ("name", "dept", "role", "status")}
    first = await client.put(f"{URL}/{code}", json={**body, "name": "先保存", "lockVersion": version})
    assert first.status_code == 200, first.text
    assert first.json()["lockVersion"] == version + 1
    second = await client.put(f"{URL}/{code}", json={**body, "name": "过期编辑", "lockVersion": version})
    assert second.status_code == 409 and second.json()["code"] == "VERSION_CONFLICT"
    assert second.json()["details"] == {"expected": version, "actual": version + 1}
    current = (await client.get(URL, params={"kw": code})).json()["items"][0]
    assert current["name"] == "先保存" and current["lockVersion"] == version + 1
    # 不带版本的旧调用仍可用；下一次带最新版本的编辑正常保存。
    old_client = await client.put(f"{URL}/{code}", json={**body, "name": "兼容旧调用"})
    assert old_client.status_code == 200 and old_client.json()["lockVersion"] == version + 2
    latest = await client.put(
        f"{URL}/{code}", json={**body, "name": "重新编辑", "lockVersion": old_client.json()["lockVersion"]}
    )
    assert latest.status_code == 200 and latest.json()["lockVersion"] == version + 3


CSV = (
    "\ufeff姓名,账号,部门,角色\n"
    "陈思远,chen.siyuan@freshmart.cn,华东事业部 / 运营中心,业务查看\n"
    "张三,zhang.san@freshmart.cn,华东事业部,门店店长\n"
)


async def test_import_preview_csv_and_rows(client, users):
    r = await client.post(URL + ":import-preview", json={"csv": CSV})
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert [x["name"] for x in rows] == ["陈思远", "张三"]
    assert rows[0]["error"] is None and rows[1]["error"] is None  # 一级SysOrg也是有效部门
    invalid = await client.post(
        URL + ":import-preview",
        json={
            "rows": [
                {
                    "name": "无部门测试",
                    "account": "no-dept@example.invalid",
                    "dept": "FIX-NONEXISTENT-DEPARTMENT",
                    "role": "业务查看",
                }
            ]
        },
    )
    assert invalid.status_code == 200 and invalid.json()["rows"][0]["error"] is None  # 通用部门允许规范自由文本
    blank = await client.post(URL + ":import-preview", json={"rows": [{"name": "空部门测试", "account": "blank-dept@example.invalid", "dept": "", "role": "业务查看"}]})
    assert blank.status_code == 200 and "1至128" in blank.json()["rows"][0]["error"]
    rows_in = [
        {"name": "a", "account": MOCK[0]["account"], "dept": DEPTS[0], "role": "业务查看"},
        {"name": "b", "account": "b@x.cn", "dept": DEPTS[0], "role": "业务查看"},
        {"name": "c", "account": "b@x.cn", "dept": DEPTS[0], "role": "业务查看"},
        {"name": "", "account": "d@x.cn", "dept": DEPTS[0], "role": "业务查看"},
        {"name": "e", "account": "e", "dept": DEPTS[0], "role": "业务查看"},
        {"name": "f", "account": "f@x.cn", "dept": DEPTS[0], "role": "厨师"},
    ]
    errs = [
        x["error"]
        for x in (await client.post(URL + ":import-preview", json={"rows": rows_in})).json()["rows"]
    ]
    assert errs == [
        "账号已存在，导入时跳过",
        None,
        "文件内账号重复",
        "姓名为空",
        "账号不是邮箱",
        "角色不存在",
    ]
    # 不写库
    assert (await client.get(URL)).json()["total"] == 120


async def test_import(client, users):
    rows = [
        {"name": "a", "account": MOCK[0]["account"], "dept": DEPTS[0], "role": "业务查看"},
        {"name": "林晓", "account": "lin.xiao@freshmart.cn", "dept": DEPTS[1], "role": "数据分析师"},
        {"name": "周航", "account": "zhou.hang@freshmart.cn", "dept": DEPTS[2], "role": "业务查看"},
    ]
    r = await client.post(URL + ":import", json={"rows": rows})
    assert r.status_code == 200 and r.json() == {"imported": 2}
    body = (await client.get(URL, params={"status": "待激活", "kw": "freshmart"})).json()
    assert [x["name"] for x in body["items"]] == ["林晓", "周航"]
    assert (await client.get(URL)).json()["total"] == 122


def first(pred):
    return next(u for u in MOCK if pred(u))


async def test_enable_disable(client, users, committed_approval_rows):  # noqa: F811
    off = first(lambda u: u["status"] == "停用")
    r = await client.post(f"{URL}/{off['id']}:enable")
    assert r.status_code == 200 and r.json()["status"] == "启用"
    assert r.json()["pendingApprovals"] == 0

    free = first(lambda u: u["status"] == "启用" and u["id"] != "U010001")
    r = await client.post(f"{URL}/{free['id']}:disable")
    assert r.status_code == 200 and r.json()["status"] == "停用"
    # 已停用再停用：幂等
    assert (await client.post(f"{URL}/{free['id']}:disable", json={})).json()["status"] == "停用"

    busy = first(lambda u: u["status"] == "启用" and u["id"] not in ("U010001", free["id"]))
    from app.core import clock
    from app.modules.static.models import ChangeRequest, CRLevel, CRStatus

    await committed_approval_rows(
        ChangeRequest(
            code="CR-REAL-PENDING",
            title="实际待审",
            owner="创建人",
            reviewer=busy["name"],
            status=CRStatus.in_review,
            level=CRLevel.minor,
            changes=1,
            detail={"reviewerCode": busy["id"]},
            status_at=clock.now(),
            created_by="U010001",
        )
    )
    await users.flush()
    other = first(lambda u: u["status"] == "启用" and u["id"] != busy["id"])
    r = await client.post(f"{URL}/{busy['id']}:disable", json={})
    assert r.status_code == 422 and r.json()["code"] == "TRANSFER_REQUIRED"
    for bad in (busy["id"], free["id"], "U999999"):
        assert (await client.post(f"{URL}/{busy['id']}:disable", json={"transferTo": bad})).status_code == 409
    r = await client.post(f"{URL}/{busy['id']}:disable", json={"transferTo": other["id"]})
    assert r.status_code == 409 and r.json()["code"] == "TRANSFER_UNAVAILABLE"
    actual = await users.scalar(select(SysUser).where(SysUser.code == busy["id"]))
    assert actual.status == UserStatus.active
    assert (await client.post(f"{URL}/U999999:enable")).status_code == 404


async def test_writes_readonly_under_ver(client, users):
    ver = {"ver": "v2026.10.2"}
    m = MOCK[0]
    body = {"name": "x", "dept": DEPTS[0], "role": "业务查看", "status": "启用"}
    for method, path, js in [
        ("post", URL, NEW),
        ("put", f"{URL}/{m['id']}", body),
        ("post", URL + ":import-preview", {"rows": []}),
        ("post", URL + ":import", {"rows": []}),
        ("post", f"{URL}/{m['id']}:enable", None),
        ("post", f"{URL}/{m['id']}:disable", {}),
    ]:
        r = await getattr(client, method)(path, params=ver, json=js)
        assert r.status_code == 403 and r.json()["code"] == "READONLY", (path, r.text)
    # 读接口在历史版本下照常
    assert (await client.get(URL, params=ver)).status_code == 200


async def test_status_enum_roundtrip(users):
    u = await users.scalar(select(SysUser).where(SysUser.code == MOCK[0]["id"]))
    assert u.status == UserStatus(MOCK[0]["status"])
