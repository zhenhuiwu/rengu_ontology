# ruff: noqa: E501
"""变更单列表 / 详情和状态流转（Releases.vue 左侧列表、详情区按钮）。

状态机照 mock（Releases.vue move() 与按钮显示条件）：正常流转、非法流转（409 CR_STATE）、
人不对（403）、阻断 / 无改动（409）、驳回原因必填（422）、只读（403 READONLY）、未登录（401）。
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.modules.static.models import ChangeRequest, CrEvent
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1"
WANG = {"X-Demo-As": "U010001"}  # 王敏
CHEN = {"X-Demo-As": "U010002"}  # 陈立军
HE = {"X-Demo-As": "U010003"}  # 何静
LIU = {"X-Demo-As": "U010004"}  # 刘婷
SUN = {"X-Demo-As": "U010005"}  # 孙浩然
DETAILS = {
    d["id"]: {k: v for k, v in d.items() if k != "id"}
    for d in json.loads((Path(__file__).parents[1] / "seeds/extra/static/CR_DETAILS.json").read_text("utf-8"))
}

# mock/changes.ts CHANGE_REQUESTS 前 3 行（原样，含可选键缺省）
MOCK_HEAD = [
    {"id": "CR-0016", "title": "新增指标“客单价（不含税）”", "owner": "刘婷", "reviewer": "王敏", "status": "审核中",
     "level": "一般", "changes": 3, "updatedAt": "2026-10-25 14:10", "reason": "门店运营周会要看客单价，目前问数只能现算"},
    {"id": "CR-0015", "title": "有效订单数补充同义词“成交单量”", "owner": "何静", "reviewer": "刘婷", "status": "已通过",
     "level": "修订", "changes": 2, "updatedAt": "2026-10-25 10:18", "reason": "财务和电商常说“成交单量”，问数召回不到"},
    {"id": "CR-0014", "title": "调整大区预算：超 5% 增加二级审批", "owner": "刘婷", "reviewer": "陈立军", "status": "草稿",
     "level": "一般", "changes": 3, "updatedAt": "2026-10-25 11:26", "reason": "预算调整幅度超过 5% 时需要财务 BP 一起审批"},
]  # fmt: skip


@pytest.fixture
async def seeded(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await grant_fixture_roles(
            session,
            {"U010001": "R-01", "U010002": "R-02", "U010003": "R-02", "U010004": "R-02", "U010005": "R-02"},
        )
        await session.commit()
        yield session


async def one(client, id):
    r = await client.get(f"{P}/change-requests/{id}", headers=WANG)
    assert r.status_code == 200, r.text
    return r.json()


async def act(client, id, action, who, body=None):
    return await client.post(f"{P}/change-requests/{id}:{action}", json=body or {}, headers=who)


async def test_list_matches_mock(client, seeded):
    r = await client.get(P + "/change-requests", headers=WANG)
    assert r.status_code == 200
    rows = r.json()
    assert [x["id"] for x in rows] == [f"CR-{n:04d}" for n in range(16, 0, -1)]
    assert rows[:3] == MOCK_HEAD
    assert rows[9]["release"] == "v2026.10.3" and "rejectReason" not in rows[9]  # CR-0007
    assert rows[10]["rejectReason"].startswith("员工是敏感对象") and "release" not in rows[10]  # CR-0006
    drafts = (await client.get(P + "/change-requests?status=草稿", headers=WANG)).json()
    assert [x["id"] for x in drafts] == ["CR-0014", "CR-0013", "CR-0012", "CR-0011", "CR-0009"]
    assert (await client.get(P + "/change-requests?status=xx", headers=WANG)).status_code == 422
    full = (await client.get(P + "/change-requests?full=true", headers=WANG)).json()
    assert all(x["detail"] == DETAILS[x["id"]] and x["events"] == [] for x in full)


async def test_list_paged(client, seeded):
    """kw / page / size / focusId（同 Releases.vue filtered、LPAGE=17、选中单子所在页）。"""
    allr = (await client.get(P + "/change-requests", headers=WANG)).json()
    r = (await client.get(P + "/change-requests?page=1", headers=WANG)).json()
    assert (r["total"], r["page"], r["size"]) == (16, 1, 10) and r["items"] == allr[:10]
    r = (await client.get(P + "/change-requests?size=5&page=9", headers=WANG)).json()
    assert (r["page"], [x["id"] for x in r["items"]]) == (4, ["CR-0001"])  # 越界收到最后一页
    r = (await client.get(P + "/change-requests?size=5&focusId=CR-0008", headers=WANG)).json()
    assert r["page"] == 2 and "CR-0008" in [x["id"] for x in r["items"]]
    r = (await client.get(P + "/change-requests?size=5&page=3&focusId=CR-9999", headers=WANG)).json()
    assert r["page"] == 3  # focusId 不在结果里 → 按 page
    r = (await client.get(P + "/change-requests?kw=cr-001", headers=WANG)).json()
    assert [x["id"] for x in r] == [f"CR-{n:04d}" for n in range(16, 9, -1)]
    word = allr[0]["title"][:2]
    r = (
        await client.get(
            P + "/change-requests", params={"kw": word, "status": "草稿", "page": 1}, headers=WANG
        )
    ).json()
    want = [x["id"] for x in allr if word in x["title"] and x["status"] == "草稿"]
    assert [x["id"] for x in r["items"]] == want and r["total"] == len(want)
    assert (await client.get(P + "/change-requests?size=0", headers=WANG)).status_code == 422


async def test_detail(client, seeded):
    d = await one(client, "CR-0010")
    assert d["status"] == "审核中" and d["detail"] == DETAILS["CR-0010"] and d["events"] == []
    r = await client.get(P + "/change-requests/CR-9999", headers=WANG)
    assert r.status_code == 404


async def test_review_happy_path(client, seeded):
    # CR-0014 草稿（刘婷 → 陈立军）：提交 → 撤回 → 再提交 → 驳回 → 改回草稿 → 提交 → 通过 → 撤回
    r = await act(client, "CR-0014", "submit", LIU)
    assert r.status_code == 200 and r.json()["status"] == "审核中", r.text
    ev = r.json()["events"][0]
    assert ev["text"] == "刘婷 提交审核" and ev["desc"] == "审核人 陈立军" and "status" not in ev
    assert (await act(client, "CR-0014", "withdraw", LIU)).json()["status"] == "草稿"
    assert (await act(client, "CR-0014", "submit", LIU)).status_code == 200
    r = await act(client, "CR-0014", "reject", CHEN, {"rejectReason": "影响范围没写清楚"})
    d = r.json()
    assert d["status"] == "已驳回" and d["rejectReason"] == "影响范围没写清楚"
    assert d["events"][0] == {
        "time": d["updatedAt"],
        "text": "陈立军 驳回",
        "desc": "影响范围没写清楚",
        "status": "error",
    }
    r = await act(client, "CR-0014", "reopen", LIU)
    assert r.json()["status"] == "草稿" and r.json()["events"][0]["desc"] == "按驳回意见修改后重新提交"
    await act(client, "CR-0014", "submit", LIU)
    nxt = (await client.get(P + "/releases/current", headers=WANG)).json()["next"]
    r = await act(client, "CR-0014", "approve", CHEN)
    assert r.json()["status"] == "已通过"
    assert r.json()["events"][0]["text"] == "陈立军 审核通过"
    assert r.json()["events"][0]["desc"] == f"等待整版发布 {nxt}"
    r = await act(client, "CR-0014", "unapprove", CHEN)
    assert r.json()["status"] == "草稿" and r.json()["events"][0]["desc"] == f"不进入 {nxt}"
    d = await one(client, "CR-0014")
    assert [e["text"] for e in d["events"]] == [
        "陈立军 撤回审核结果", "陈立军 审核通过", "刘婷 提交审核", "刘婷 改回草稿", "陈立军 驳回",
        "刘婷 提交审核", "刘婷 撤回提交", "刘婷 提交审核",
    ]  # fmt: skip
    # 状态留在库里：列表也看到
    rows = (await client.get(P + "/change-requests", headers=WANG)).json()
    assert next(x for x in rows if x["id"] == "CR-0014")["status"] == "草稿"


async def test_approved_feeds_publish(client, seeded):
    # CR-0016 王敏审核通过后进入合并校验
    assert (await act(client, "CR-0016", "approve", WANG)).status_code == 200
    mc = (await client.post(P + "/releases:merge-check", headers=WANG)).json()
    assert mc["rows"][0]["note"] == "CR-0016、CR-0015 都改了 order_cnt"  # 通过后才参与合并校验


@pytest.mark.parametrize(
    "id,action,who",
    [
        ("CR-0014", "approve", CHEN),  # 草稿不能审核
        ("CR-0014", "withdraw", LIU),  # 草稿不能撤回提交
        ("CR-0014", "reopen", LIU),  # 草稿不能改回草稿
        ("CR-0016", "submit", LIU),  # 审核中不能再提交
        ("CR-0016", "unapprove", WANG),  # 审核中不是已通过
        ("CR-0015", "approve", LIU),  # 已通过不能再审核
        ("CR-0007", "unapprove", WANG),  # 已发布不能撤回
        ("CR-0006", "submit", SUN),  # 已驳回要先改回草稿
    ],
)
async def test_illegal_transition(client, seeded, id, action, who):
    before = await one(client, id)
    r = await act(client, id, action, who, {"rejectReason": "x"})
    assert r.status_code == 409 and r.json()["code"] == "CR_STATE", r.text
    assert await one(client, id) == before


@pytest.mark.parametrize(
    "id,action,who",
    [
        ("CR-0016", "approve", LIU),  # 提交人不能审自己的单子
        ("CR-0016", "approve", CHEN),  # 不是审核人
        ("CR-0016", "reject", HE),  # 不是审核人
        ("CR-0016", "withdraw", WANG),  # 审核人不能撤回提交
        ("CR-0015", "unapprove", CHEN),  # 不是 owner / reviewer
        ("CR-0006", "reopen", CHEN),  # 不是 owner / reviewer
    ],
)
async def test_transition_permission(client, seeded, id, action, who):
    before = await one(client, id)
    r = await act(client, id, action, who, {"rejectReason": "x"})
    assert r.status_code == 403, r.text
    assert await one(client, id) == before


async def test_gates_and_reason(client, seeded):
    # 有阻断不能提交（找一张带阻断的草稿）
    blocked = [k for k, d in DETAILS.items() if any(c["result"] == "阻断" for c in d["checks"])]
    rows = {x["id"]: x for x in (await client.get(P + "/change-requests", headers=WANG)).json()}
    drafts = [k for k in blocked if rows[k]["status"] == "草稿"]
    assert drafts, blocked
    r = await act(client, drafts[0], "submit", WANG)
    assert r.status_code == 409 and r.json()["code"] == "CR_BLOCKED"
    # 没改动不能提交
    cr = await seeded.scalar(select(ChangeRequest).where(ChangeRequest.code == "CR-0013"))
    cr.changes = 0
    await seeded.commit()
    r = await act(client, "CR-0013", "submit", WANG)
    assert r.status_code == 409 and r.json()["code"] == "CR_EMPTY"
    # 驳回原因必填
    r = await act(client, "CR-0016", "reject", WANG, {"rejectReason": " "})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    assert (await one(client, "CR-0016"))["status"] == "审核中"


async def test_delete_draft(client, seeded):
    r = await client.delete(P + "/change-requests/CR-0013", headers=CHEN)  # 王敏的草稿
    assert r.status_code == 403
    r = await client.delete(P + "/change-requests/CR-0016", headers=LIU)  # 审核中
    assert r.status_code == 409 and r.json()["code"] == "CR_STATE"
    await act(client, "CR-0013", "submit", WANG)
    await act(client, "CR-0013", "withdraw", WANG)
    r = await client.delete(P + "/change-requests/CR-0013", headers=WANG)
    assert r.status_code == 204
    assert (await client.get(P + "/change-requests/CR-0013", headers=WANG)).status_code == 404
    n = await seeded.scalar(select(func.count()).select_from(ChangeRequest))
    assert n == 15
    assert await seeded.scalar(select(func.count()).select_from(CrEvent)) == 0  # 级联删掉


async def test_cr_writes_readonly_and_login(client, seeded):
    for q in ("?ver=v2026.10.2", "?previewAs=U010002"):
        r = await client.post(f"{P}/change-requests/CR-0016:approve{q}", json={}, headers=WANG)
        assert r.status_code == 403 and r.json()["code"] == "READONLY", r.text
        r = await client.delete(f"{P}/change-requests/CR-0013{q}", headers=WANG)
        assert r.status_code == 403 and r.json()["code"] == "READONLY", r.text
    r = await act(client, "CR-0016", "approve", {"X-Demo-As": "U999999"})
    assert r.status_code == 401
    assert (await one(client, "CR-0016"))["status"] == "审核中"
