"""告警处理：每个迁移、每个错误码、只读、在途锁、发起动作、动作成功后自动转已解决。"""

import pytest
from sqlalchemy import select, update

from app.modules.dynamic.models import Alert, AlertStatus
from tests.dynamic_support import API, dyn, err  # noqa: F401
from tests.test_use_ops_runtime import alert_runtime, runtime  # noqa: F401

OPEN = "AL-20261025-0126"  # R-007 商品，待处理，无关联动作
OPEN_ORDER = "AL-20261025-0131"  # R-015 订单，待处理
OPEN_LINKED = "AL-20261025-0139"  # 待处理，关联动作待确认（在途）
MINE_LOCKED = "AL-20261025-0127"  # 王敏处理中，关联动作待确认（在途）
OTHERS = "AL-20261025-0136"  # 陈立军处理中，关联动作执行中
FAILED_LINK = "AL-20261024-0118"  # 刘婷处理中，关联动作失败（不在途）


async def test_list_filters(dyn, client):  # noqa: F811
    r = (await client.get(f"{API}/alerts", params={"status": "doing"})).json()
    assert r["total"] == 4 and {x["status"] for x in r["items"]} == {"处理中"}
    r = (await client.get(f"{API}/alerts", params={"status": "closed", "ruleId": "R-012", "size": 5})).json()
    assert r["size"] == 5 and all(
        x["ruleId"] == "R-012" and x["status"] in ("已解决", "已忽略") for x in r["items"]
    )
    r = (await client.get(f"{API}/alerts", params={"status": "all", "level": "高"})).json()
    assert r["total"] > 0 and all(x["level"] == "高" for x in r["items"])
    err(
        await client.get(f"{API}/alerts", params={"status": "all", "level": "严重"}), 422, "VALIDATION_FAILED"
    )
    r = (await client.get(f"{API}/alerts", params={"status": "all", "kw": "0126"})).json()
    assert [x["id"] for x in r["items"]] == [OPEN]
    r = (await client.get(f"{API}/alerts", params={"status": "all", "kw": "%"})).json()
    assert r["total"] == 0  # LIKE 转义
    err(await client.get(f"{API}/alerts", params={"status": "bad"}), 422, "VALIDATION_FAILED")
    err(await client.get(f"{API}/alerts", params={"size": 0}), 422, "VALIDATION_FAILED")


async def test_detail(dyn, client):  # noqa: F811
    d = (await client.get(f"{API}/alerts/{MINE_LOCKED}")).json()
    assert d["actionStatus"] == "待确认" and d["actionId"] == "ACT-20261025-0550" and d["locked"] is True
    assert d["rule"]["id"] == "R-007" and d["recent7d"] >= 1 and d["log"]
    assert d["can"]["close"] is False and "有结果前不能关闭" in d["can"]["reasons"]["close"]
    assert d["can"]["transfer"] is False and d["can"]["startAction"] is False
    o = (await client.get(f"{API}/alerts/{OPEN}")).json()
    assert o["can"] == {"claim": True, "transfer": True, "close": False, "startAction": False, "reasons": {}}
    err(await client.get(f"{API}/alerts/AL-NOPE"), 404, "NOT_FOUND")


async def test_claim_and_state_invalid(dyn, client):  # noqa: F811
    r = await client.post(f"{API}/alerts/{OPEN}:claim")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "处理中" and d["assignee"] == "王敏"
    assert d["log"][-1]["text"] == "认领，状态 待处理 → 处理中" and d["log"][-1]["who"] == "王敏"
    b = err(await client.post(f"{API}/alerts/{OPEN}:claim"), 409, "STATE_INVALID")
    assert b["details"] == {"from": "处理中", "to": "处理中"}


async def test_batch_claim(dyn, client):  # noqa: F811
    r = await client.post(f"{API}/alerts:batch-claim", json={"ids": [OPEN, OPEN_ORDER, OTHERS]})
    assert r.json() == {"claimed": 2, "ids": [OPEN, OPEN_ORDER]}
    err(await client.post(f"{API}/alerts:batch-claim", json={"ids": []}), 409, "EMPTY_INPUT")
    err(await client.post(f"{API}/alerts:batch-claim", json={"ids": ["AL-X"]}), 404, "NOT_FOUND")


async def test_transfer(dyn, client):  # noqa: F811
    people = (await client.get(f"{API}/people", params={"alertId": OPEN})).json()
    candidates = [p for p in people if p["id"] != "U010001"]
    assert len(candidates) >= 2
    first, second = candidates[:2]
    for target in (first, second):
        response = await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": target["id"]})
        assert response.status_code == 200, response.text
        assert response.json()["assigneeId"] == target["id"]
        assert target["id"] in response.json()["log"][-1]["text"]
        assert await dyn.scalar(select(Alert.assignee).where(Alert.code == OPEN)) == target["id"]
    err(
        await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": second["id"]}),
        422,
        "VALIDATION_FAILED",
    )
    err(await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": "路人甲"}), 422, "VALIDATION_FAILED")
    err(await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": " "}), 422, "VALIDATION_FAILED")
    err(
        await client.post(f"{API}/alerts/{OPEN_LINKED}:transfer", json={"to": first["id"]}),
        409,
        "ALERT_LOCKED_BY_ACTION",
    )
    closed = (await client.get(f"{API}/alerts", params={"status": "closed", "size": 1})).json()["items"][0][
        "id"
    ]
    err(await client.post(f"{API}/alerts/{closed}:transfer", json={"to": first["id"]}), 409, "STATE_INVALID")


async def test_close_paths(dyn, client):  # noqa: F811
    await dyn.execute(update(Alert).where(Alert.code == FAILED_LINK).values(assignee="U010001"))
    me = {"X-Demo-As": "U010001"}
    d = (
        await client.post(
            f"{API}/alerts/{FAILED_LINK}:close", json={"as": "已忽略", "reason": "重复"}, headers=me
        )
    ).json()
    assert d["status"] == "已忽略" and d["log"][-1]["text"] == "关闭为已忽略：重复（处理中 → 已忽略）"
    # 再关：已关闭 → 409
    err(
        await client.post(
            f"{API}/alerts/{FAILED_LINK}:close", json={"as": "已解决", "reason": "x"}, headers=me
        ),
        409,
        "STATE_INVALID",
    )
    # 待处理不能关
    err(
        await client.post(f"{API}/alerts/{OPEN}:close", json={"as": "已解决", "reason": "x"}),
        409,
        "STATE_INVALID",
    )
    # 非处理人
    err(
        await client.post(f"{API}/alerts/{OTHERS}:close", json={"as": "已解决", "reason": "x"}),
        403,
        "NOT_ASSIGNEE",
    )
    # 在途锁
    err(
        await client.post(f"{API}/alerts/{MINE_LOCKED}:close", json={"as": "已解决", "reason": "x"}),
        409,
        "ALERT_LOCKED_BY_ACTION",
    )
    # 入参校验
    err(
        await client.post(f"{API}/alerts/{OPEN}:close", json={"as": "已关闭", "reason": "x"}),
        422,
        "VALIDATION_FAILED",
    )
    err(
        await client.post(f"{API}/alerts/{OPEN}:close", json={"as": "已解决", "reason": ""}),
        422,
        "VALIDATION_FAILED",
    )


async def test_batch_close(dyn, client):  # noqa: F811
    closed_before = (await client.get(f"{API}/alerts")).json()["counts"]["closed"]
    await client.post(f"{API}/alerts:batch-claim", json={"ids": [OPEN, OPEN_ORDER]})
    r = await client.post(
        f"{API}/alerts:batch-close",
        json={"ids": [OPEN, OPEN_ORDER, OTHERS, MINE_LOCKED], "as": "已解决", "reason": "已处理"},
    )
    # 在途项与单条关闭一致，不能借批量绕过。
    assert r.json() == {"closed": 2, "ids": [OPEN, OPEN_ORDER]}
    counts = (await client.get(f"{API}/alerts")).json()["counts"]
    assert counts["closed"] == closed_before + 2
    err(
        await client.post(f"{API}/alerts:batch-close", json={"ids": [], "as": "已解决", "reason": "x"}),
        409,
        "EMPTY_INPUT",
    )


async def test_readonly(dyn, client):  # noqa: F811
    for path, body in [
        (f"{API}/alerts/{OPEN}:claim", None),
        (f"{API}/alerts:batch-claim", {"ids": [OPEN]}),
        (f"{API}/alerts/{OPEN}:transfer", {"to": "刘婷"}),
        (f"{API}/alerts/{OPEN}:start-action", None),
    ]:
        err(await client.post(path, params={"ver": "v2026.10.1"}, json=body), 403, "READONLY")
        err(await client.post(path, params={"previewAs": "U010231"}, json=body), 403, "READONLY")
    assert (await client.get(f"{API}/alerts/{OPEN}", params={"ver": "v2026.10.1"})).json()[
        "status"
    ] == "待处理"


async def test_start_action_full_flow(alert_runtime, client):  # noqa: F811
    session, action, _ = alert_runtime
    action.approval = "U010001"
    await session.flush()
    await client.post(f"{API}/alerts/OPS-A00:claim")
    response = await client.post(f"{API}/alerts/OPS-A00:start-action", json={"params": {"enabled": True}})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["alert"]["locked"] is True and out["action"]["status"] == "审批中"
    assert out["action"]["source"]["alertId"] == "OPS-A00" and out["action"]["confirmer"] == "王敏"
    code = out["action"]["id"]
    approved = await client.post(f"{API}/action-runs/{code}:approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "成功" and approved.json()["code"] == "INTERNAL"
    assert (await client.get(f"{API}/alerts/OPS-A00")).json()["status"] == "已解决"


async def test_start_action_order_approver(alert_runtime, client):  # noqa: F811
    # Existing name retained: explicit exemption must never invent an approver.
    await client.post(f"{API}/alerts/OPS-A00:claim")
    response = await client.post(f"{API}/alerts/OPS-A00:start-action", json={"params": {"enabled": True}})
    assert response.status_code == 200, response.text
    assert response.json()["action"]["approver"] is None
    assert response.json()["action"]["status"] == "成功"


async def test_start_action_errors(alert_runtime, client):  # noqa: F811
    session, _, rule = alert_runtime
    body = {"params": {}}
    err(await client.post(f"{API}/alerts/OPS-A00:start-action", json=body), 409, "STATE_INVALID")
    other = await session.scalar(select(Alert).where(Alert.code == "OPS-A01"))
    other.status, other.assignee = AlertStatus.processing, "OPS-LIMIT"
    await session.flush()
    err(await client.post(f"{API}/alerts/OPS-A01:start-action", json=body), 403, "NOT_ASSIGNEE")
    await client.post(f"{API}/alerts/OPS-A00:claim")
    err(await client.post(f"{API}/alerts/OPS-A00:start-action", json=body), 422, "SUBMISSION_CRITERIA_FAILED")
    assert await session.scalar(select(Alert.action_run_id).where(Alert.code == "OPS-A00")) is None
    rule.action_type_id = None
    await session.flush()
    err(await client.post(f"{API}/alerts/OPS-A00:start-action", json=body), 409, "STATE_INVALID")


@pytest.mark.parametrize("path", ["/alerts", "/action-runs"])
async def test_page_params_come_from_skeleton(dyn, client, path):  # noqa: F811
    """页码交给骨架 PageParams / Page.of（paginate.ts:12-13）：默认条数照前端，page ≤ 0 → 1，
    越界 → 最后一页（响应 page 同步）。收拢是骨架行为，dynamic 只透传。"""
    q = {"status": "all"} if path == "/alerts" else {}
    assert (await client.get(f"{API}{path}")).json()["size"] == 10
    first = (await client.get(f"{API}{path}", params={**q, "size": 5})).json()
    assert (await client.get(f"{API}{path}", params={**q, "size": 5, "page": 0})).json() == first
    last = -(-first["total"] // 5) or 1
    far = (await client.get(f"{API}{path}", params={**q, "size": 5, "page": 999})).json()
    exact = (await client.get(f"{API}{path}", params={**q, "size": 5, "page": last})).json()
    assert far["page"] == last and far["items"] == exact["items"] and far["items"]


async def test_generic_dims_and_approver(dyn, client):  # noqa: F811
    """去零售写死：门店是通用维度 dims；对象称呼取对象类型目录；审批人取动作类型配置。"""
    o = (await client.get(f"{API}/alerts/{OPEN}")).json()
    assert "storeId" not in o and "storeLabel" not in o
    assert o["objKind"] == "product" and o["objKindLabel"] == "商品"
    assert len(o["dims"]) == 1 and o["dims"][0]["objKind"] == "store" and o["dims"][0]["kindLabel"] == "门店"
    assert o["dims"][0]["id"] and o["dims"][0]["label"]
    assert o["approver"] is None  # 现有文字未定位真实单个审批人，不能读JSON补审批路由。
    claimed = await client.post(f"{API}/alerts/{OPEN}:claim")
    assert claimed.status_code == 200
    unconfigured = (await client.get(f"{API}/alerts/{OPEN}")).json()
    assert unconfigured["can"]["startAction"] is False
    assert "审批配置" in unconfigured["can"]["reasons"]["startAction"]
    err(
        await client.post(f"{API}/alerts/{OPEN}:start-action", json={"params": {}}),
        503,
        "ACTION_APPROVAL_NOT_CONFIGURED",
    )
    d = (await client.get(f"{API}/alerts/{OPEN_ORDER}")).json()
    assert d["dims"] == [] and d["objKindLabel"] == "订单" and d["approver"] is None
    r = (await client.get(f"{API}/alerts", params={"status": "all", "ruleId": "R-021", "size": 1})).json()
    assert r["items"][0]["dims"] == [] and r["items"][0]["objKindLabel"] == "门店"
    detail = (await client.get(f"{API}/alerts/{r['items'][0]['id']}")).json()
    assert detail["approver"] is None  # R-021 未绑定动作


async def test_facets_and_multi_filters(dyn, client):  # noqa: F811
    """分面计数与列表 total 一致；多选 / 对象类型 / 维度值筛选；分面不受自身选择影响。"""

    async def total(**params):
        r = await client.get(f"{API}/alerts", params={"status": "all", "size": 1, **params})
        assert r.status_code == 200, r.text
        return r.json()["total"]

    f = (await client.get(f"{API}/alerts/facets", params={"status": "all"})).json()
    everything = await total()
    assert sum(x["count"] for x in f["level"]) == everything
    assert sum(x["count"] for x in f["objKind"]) == everything
    assert sum(x["count"] for x in f["ruleId"]) == everything
    for key in ("level", "objKind", "ruleId"):
        for x in f[key][:2]:
            assert await total(**{key: x["value"]}) == x["count"], (key, x)
    two = [x["value"] for x in f["ruleId"][:2]]
    assert await total(ruleId=",".join(two)) == sum(x["count"] for x in f["ruleId"][:2])

    assert f["dims"], "种子告警应带维度"
    kind = f["dims"][0]["objKind"]
    val = f["dims"][0]["values"][0]
    assert await total(dim=f"{kind}:{val['id']}") == val["count"]
    picked = {"status": "all", "dim": f"{kind}:{val['id']}"}
    g = (await client.get(f"{API}/alerts/facets", params=picked)).json()
    assert sum(x["count"] for x in g["level"]) == val["count"]
    same = next(d for d in g["dims"] if d["objKind"] == kind)  # 自身维度不按已选值收窄
    assert {v["id"] for v in same["values"]} == {v["id"] for v in f["dims"][0]["values"]}

    lv = f["level"][0]["value"]
    h = (await client.get(f"{API}/alerts/facets", params={"status": "all", "level": lv})).json()
    assert h["level"] == f["level"]  # 等级分面忽略自身选择
    assert sum(x["count"] for x in h["objKind"]) == await total(level=lv)

    err(await client.get(f"{API}/alerts", params={"dim": "nokind"}), 422, "VALIDATION_FAILED")
    err(await client.get(f"{API}/alerts/facets", params={"status": "bad"}), 422, "VALIDATION_FAILED")


async def test_object_property_filters(dyn, client):  # noqa: F811
    """filters=<JSON> 按告警对象的业务属性筛选：取值来自对象类型分面；列表 / 分面计数一致；422 同 static。"""
    import json

    kinds = (await client.get(f"{API}/alerts/facets", params={"status": "all"})).json()["objKind"]
    kind = next(k for k in kinds if k["count"])
    props = (await client.get(f"{API}/object-types/{kind['value']}/facets")).json()
    prop = next(p for p in props if p["kind"] == "enum" and p["values"])
    seen = 0
    for v in prop["values"]:
        params = {"status": "all", "size": 100, "objKind": kind["value"]}
        params["filters"] = json.dumps({prop["key"]: [v["value"]]}, ensure_ascii=False)
        r = await client.get(f"{API}/alerts", params=params)
        assert r.status_code == 200, r.text
        page = r.json()
        assert all(x["objKind"] == kind["value"] for x in page["items"])
        seen += page["total"]
        params.pop("size")
        f = (await client.get(f"{API}/alerts/facets", params=params)).json()
        assert sum(x["count"] for x in f["level"]) == page["total"]
        assert sum(x["count"] for x in f["objKind"]) == page["total"]  # 对象类型分面同样受 filters 约束
    assert 0 < seen <= kind["count"]

    good = json.dumps({prop["key"]: [prop["values"][0]["value"]]}, ensure_ascii=False)
    for bad in (
        {"filters": good},  # 未给 objKind
        {"filters": good, "objKind": ",".join(k["value"] for k in kinds[:2])},  # 多个对象类型
        {"filters": "{bad", "objKind": kind["value"]},  # 非 JSON
        {"filters": json.dumps({"no_such_prop": ["x"]}), "objKind": kind["value"]},  # 不可筛选
        {"filters": json.dumps({prop["key"]: "scalar"}), "objKind": kind["value"]},  # 枚举须给数组
    ):
        for path in ("/alerts", "/alerts/facets"):
            err(await client.get(f"{API}{path}", params={"status": "all", **bad}), 422, "VALIDATION_FAILED")
