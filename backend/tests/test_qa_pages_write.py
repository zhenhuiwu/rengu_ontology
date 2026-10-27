"""R2 我的页面写接口 P2、P4–P10（qa-pages 流）。"""

from datetime import datetime

import pytest

from app.core.schema import _tz
from app.modules.qa.pages_api import next_run
from tests import qa_page_test_support
from tests.function_access_support import grant_fixture_roles
from tests.qa_access_support import seed_qa_access

qa_workspace = qa_page_test_support.qa_workspace
users = qa_page_test_support.users

B = "/api/qa/v1"
CHEN = {"X-Demo-As": "U010763"}  # 陈杰：P001 可编辑成员
LIU = {"X-Demo-As": "U010004"}  # 刘婷：P004 可查看成员


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await grant_fixture_roles(users, {"U010004": "R-05"})
    await users.flush()
    return client


async def test_create_blank_page_defaults(qa):
    r = await qa.post(f"{B}/pages", json={"name": " 华东复购分析 ", "scope": "当前数据权限"})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["id"].startswith("P") and p["name"] == "华东复购分析" and p["sourceKind"] == "手动新建"
    assert p["cards"] == [] and p["status"] == "正常"
    assert p.get("dataAsOf") is None and p.get("lastRefresh") is None
    assert p["refresh"] == {"freq": "手动", "time": "--", "onFail": "保留上次结果"}
    assert p["share"] == {"mode": "仅自己", "members": [], "link": False, "allowExport": True}
    assert p["sub"]["on"] is False and p["sub"]["channel"] == "邮件"
    found = (await qa.get(f"{B}/pages", params={"kw": p["name"]})).json()["items"]
    assert found[0]["id"] == p["id"]
    dup = await qa.post(f"{B}/pages", json={"name": "华东复购分析"})
    assert dup.status_code == 409 and dup.json()["code"] == "DUPLICATE_KEY"


async def test_create_from_template(qa):
    body = {
        "name": "我的周报",
        "scope": "华东大区",
        "template": {
            "id": "T001",
            "caliber": "不含税",
            "refresh": {"freq": "每周", "weekday": "周一", "time": "08:00"},
        },
    }
    before = (await qa.get(f"{B}/pages")).json()["total"]
    template = (await qa.get(f"{B}/templates/T001")).json()
    r = await qa.post(f"{B}/pages", json=body)
    assert r.status_code == 422 and r.json()["code"] == "PAGE_EXECUTION_UNAVAILABLE"
    assert (await qa.get(f"{B}/pages")).json()["total"] == before
    assert (await qa.get(f"{B}/templates/T001")).json()["uses"] == template["uses"]
    assert (await qa.post(f"{B}/pages", json={"name": "x", "template": {"id": "T999"}})).status_code == 404


async def test_rename_and_permissions(qa):
    r = await qa.patch(f"{B}/pages/P002", json={"name": "门店履约看板 v2"})
    assert r.status_code == 200 and r.json()["name"] == "门店履约看板 v2"
    r = await qa.patch(f"{B}/pages/P002", json={"name": "华东大区销售周报"})
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_KEY"
    # 可编辑成员能改名，只读成员 403，看不到的 404
    share = {
        "mode": "指定成员",
        "members": [{"name": "陈杰", "perm": "可编辑"}],
        "link": False,
        "allowExport": True,
        "lockVersion": (await qa.get(f"{B}/pages/P002")).json()["lockVersion"],
    }
    assert (await qa.put(f"{B}/pages/P002/share", json=share)).status_code == 200
    assert (await qa.patch(f"{B}/pages/P002", json={"name": "周报"}, headers=CHEN)).status_code == 200
    assert (await qa.put(f"{B}/pages/P002/share", json=share, headers=CHEN)).status_code == 403
    assert (await qa.patch(f"{B}/pages/P004", json={"name": "y"}, headers=LIU)).status_code == 403
    assert (await qa.patch(f"{B}/pages/P002", json={"name": "y"}, headers=LIU)).status_code == 404
    # 删除、共享只有拥有者
    assert (await qa.delete(f"{B}/pages/P002", headers=CHEN)).status_code == 403


async def test_delete_page(qa):
    assert (await qa.delete(f"{B}/pages/P004")).status_code == 204
    assert (await qa.get(f"{B}/pages/P004")).status_code == 404
    assert (await qa.get(f"{B}/pages/P004", headers=LIU)).status_code == 404


async def test_refresh_unavailable_preserves_snapshot(qa):
    before = (await qa.get(f"{B}/pages/P005")).json()
    response = await qa.post(f"{B}/pages/P005:refresh")
    assert response.status_code == 200 and response.json()["status"] == "刷新失败"
    after = (await qa.get(f"{B}/pages/P005")).json()
    for key in ("dataAsOf", "lastRefresh", "cards"):
        assert after.get(key) == before.get(key)


async def test_pin_card_from_saved_result(qa, users):
    from sqlalchemy import func, select

    from app.modules.qa.models import QaTurn, TurnKind

    source = await users.scalar(select(QaTurn).where(QaTurn.code == "T-01"))
    seq = await users.scalar(select(func.max(QaTurn.seq)).where(QaTurn.session_id == source.session_id))
    users.add(
        QaTurn(
            code="T-DB-TABLE",
            session_id=source.session_id,
            seq=seq + 1,
            kind=TurnKind.answer,
            question="数据库已保存结果",
            snapshot={
                "answer": {
                    "card": "table",
                    "metric": "已保存金额 v7",
                    "table": {
                        "columns": [
                            {"key": "store", "title": "门店"},
                            {"key": "amount", "title": "金额", "align": "right"},
                        ],
                        "items": [{"store": "真实结果", "amount": 23}],
                    },
                }
            },
        )
    )
    await users.flush()
    response = await qa.post(f"{B}/pages/P002/cards", json={"turnId": "T-DB-TABLE", "title": "已保存结果"})
    assert response.status_code == 422 and response.json()["code"] == "PAGE_EXECUTION_UNAVAILABLE", response.text
    for turn in ("T-04", "T-01", "T-02"):
        bad = await qa.post(f"{B}/pages/P002/cards", json={"turnId": turn, "title": "x"})
        assert bad.status_code == 422 and bad.json()["code"] == "TURN_NOT_PINNABLE"
    assert (await qa.post(f"{B}/pages/P002/cards", json={"turnId": "NOPE", "title": "x"})).status_code == 404
    blank = await qa.post(f"{B}/pages/P002/cards", json={"turnId": "T-DB-TABLE", "title": "   "})
    assert blank.status_code == 409 and blank.json()["code"] == "EMPTY_INPUT"


async def test_reorder_and_remove_cards(qa):
    keys = [c["key"] for c in (await qa.get(f"{B}/pages/P001")).json()["cards"]]
    order = [keys[2], keys[0], keys[1]]
    r = await qa.put(f"{B}/pages/P001/cards", json={"order": order})
    assert r.status_code == 200, r.text
    assert [c["key"] for c in r.json()["cards"]] == order and r.json()["removed"] == [keys[3]]
    assert [c["key"] for c in (await qa.get(f"{B}/pages/P001")).json()["cards"]] == order
    bad = await qa.put(f"{B}/pages/P001/cards", json={"order": ["ZZZ"]})
    assert bad.status_code == 422


async def test_schedule(qa):
    body = {
        "refresh": {"freq": "每天", "time": "07:30", "onFail": "标记失败并通知"},
        "sub": {
            "on": True,
            "channel": "企业微信",
            "receivers": ["陈杰"],
            "when": "每天 09:00",
            "onlyChanged": True,
        },
    }
    before = (await qa.get(f"{B}/pages/P002")).json()
    r = await qa.put(f"{B}/pages/P002/schedule", json=body)
    assert r.status_code == 422, r.text
    p = (await qa.get(f"{B}/pages/P002")).json()
    assert p["sub"] == before["sub"] and p["refresh"] == before["refresh"]
    body["sub"]["receivers"] = []
    assert (await qa.put(f"{B}/pages/P002/schedule", json=body)).status_code == 422
    body["refresh"] = {"freq": "手动", "time": "--"}
    body["sub"]["on"] = False
    assert (await qa.put(f"{B}/pages/P002/schedule", json=body)).json()["nextRun"] == "不自动刷新"


def test_next_run_rules():
    now = datetime(2026, 10, 25, 17, 10, tzinfo=_tz())  # 周日
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M")  # noqa: E731
    assert fmt(next_run({"freq": "每小时"}, now)) == "2026-10-25 18:00"
    assert fmt(next_run({"freq": "每天", "time": "08:00"}, now)) == "2026-10-26 08:00"
    assert fmt(next_run({"freq": "每周", "weekday": "周一", "time": "08:00"}, now)) == "2026-10-26 08:00"
    assert fmt(next_run({"freq": "每月", "monthday": 1, "time": "08:00"}, now)) == "2026-11-01 08:00"
    assert next_run({"freq": "手动"}, now) is None


async def test_share(qa):
    async def save(body):
        version = (await qa.get(f"{B}/pages/P002")).json()["lockVersion"]
        return await qa.put(f"{B}/pages/P002/share", json={**body, "lockVersion": version})

    body = {
        "mode": "指定成员",
        "members": [{"name": "陈杰", "dept": "", "perm": "可查看"}],
        "link": True,
        "allowExport": True,
    }
    r = await save(body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (
        d["mode"] == "指定成员" and d["members"][0]["userId"] == "U010763" and d["linkUrl"].endswith("page_id=P002")
    )
    assert d["newViewers"] == ["陈杰（可查看）"] and len(d["newGrants"]) == 2
    assert (await qa.get(f"{B}/pages/P002", headers=CHEN)).status_code == 200  # 被共享人能打开
    # 仅自己：link 强制关、成员清空
    d = (await save({**body, "mode": "仅自己"})).json()
    assert d["link"] is False and d["members"] == [] and "linkUrl" not in d
    assert (await qa.get(f"{B}/pages/P002", headers=CHEN)).status_code == 404
    r = await save({**body, "members": [{"name": "查无此人", "perm": "可查看"}]})
    assert r.status_code == 422 and r.json()["code"] == "USER_NOT_FOUND"
    r = await save({**body, "members": []})
    assert r.status_code == 409 and r.json()["code"] == "EMPTY_INPUT"


async def test_writes_readonly(qa):
    r = await qa.post(f"{B}/pages?ver=v2026.10.2", json={"name": "x"})
    assert r.status_code == 403 and r.json()["code"] == "READONLY"
    r = await qa.put(f"{B}/pages/P002/share?previewAs=U010004", json={"mode": "仅自己"})
    assert r.status_code == 403 and r.json()["code"] == "READONLY"
