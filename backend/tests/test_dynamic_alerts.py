"""告警处理：每个迁移、每个错误码、只读、在途锁、发起动作、动作成功后自动转已解决。"""

from sqlalchemy import select, update

from app.modules.dynamic.models import Alert
from tests.dynamic_support import API, FailingChecker, as_, dyn, err, fakes  # noqa: F401

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
    assert all(x["level"] == "高" for x in r["items"])
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
    d = (await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": "刘婷"})).json()
    assert d["status"] == "处理中" and d["assignee"] == "刘婷"
    assert d["log"][-1]["text"] == "转派：未认领 → 刘婷"
    d = (await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": "何静"})).json()
    assert d["log"][-1]["text"] == "转派：刘婷 → 何静"
    b = err(await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": "何静"}), 422, "VALIDATION_FAILED")
    assert b["details"]["fields"][0]["field"] == "to"
    err(await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": "路人甲"}), 422, "VALIDATION_FAILED")
    err(await client.post(f"{API}/alerts/{OPEN}:transfer", json={"to": " "}), 422, "VALIDATION_FAILED")
    err(
        await client.post(f"{API}/alerts/{OPEN_LINKED}:transfer", json={"to": "刘婷"}),
        409,
        "ALERT_LOCKED_BY_ACTION",
    )
    closed = (await client.get(f"{API}/alerts", params={"status": "closed", "size": 1})).json()["items"][0][
        "id"
    ]
    err(await client.post(f"{API}/alerts/{closed}:transfer", json={"to": "刘婷"}), 409, "STATE_INVALID")


async def test_close_paths(dyn, client):  # noqa: F811
    me = await as_(dyn, "刘婷")
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
    await client.post(f"{API}/alerts:batch-claim", json={"ids": [OPEN, OPEN_ORDER]})
    r = await client.post(
        f"{API}/alerts:batch-close",
        json={"ids": [OPEN, OPEN_ORDER, OTHERS, MINE_LOCKED], "as": "已解决", "reason": "已处理"},
    )
    # 只关本人处理中的（照前端 closable，不查在途锁）
    assert r.json() == {"closed": 3, "ids": [OPEN, OPEN_ORDER, MINE_LOCKED]}
    counts = (await client.get(f"{API}/alerts")).json()["counts"]
    assert counts["closed"] == 149
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


async def test_start_action_full_flow(dyn, client, fakes):  # noqa: F811
    await client.post(f"{API}/alerts/{OPEN}:claim")
    r = await client.post(f"{API}/alerts/{OPEN}:start-action")
    assert r.status_code == 200, r.text
    out = r.json()
    a, act = out["alert"], out["action"]
    assert a["actionRef"] == "act-al-20261025-0126" and a["actionStatus"] == "审批中" and a["locked"] is True
    assert (
        a["log"][-1]["text"] == "发起动作「发起调价审批」，待品类经理 王敏审批；动作成功后告警自动转为已解决"
    )
    assert act["status"] == "审批中" and act["idemKey"] == "act-al-20261025-0126"
    assert act["source"]["type"] == "告警" and act["source"]["alertId"] == OPEN
    assert act["id"] == "ACT-20261025-3850" and act["confirmer"] == "王敏"  # 当天最大号 +1（含 RUNS 历史）
    assert [x["text"] for x in act["log"]][-2:] == ["确认提交", "等待审批"]
    # 再发起：已有关联动作
    err(await client.post(f"{API}/alerts/{OPEN}:start-action"), 409, "STATE_INVALID")
    # 审批 → 成功 → 告警自动已解决
    d = (await client.post(f"{API}/action-runs/{act['id']}:approve")).json()
    assert d["status"] == "成功" and d["extRef"] == "OA-1"
    assert fakes.submitter.calls == [(act["id"], "act-al-20261025-0126")]
    assert [e[1] for e in fakes.emitter.events] == ["ActionExecuted"]
    al = (await client.get(f"{API}/alerts/{OPEN}")).json()
    last = al["log"][-1]
    assert al["status"] == "已解决" and al["locked"] is False
    assert (last["text"], last["who"]) == ("关联动作执行成功，状态 处理中 → 已解决", "系统")


async def test_start_action_order_approver(dyn, client, fakes):  # noqa: F811
    await client.post(f"{API}/alerts/{OPEN_ORDER}:claim")
    act = (await client.post(f"{API}/alerts/{OPEN_ORDER}:start-action")).json()["action"]
    assert act["approver"] == "区域运营 陈立军" and act["action"] == "发起物流催派"


async def test_start_action_errors(dyn, client, fakes):  # noqa: F811
    err(await client.post(f"{API}/alerts/{OPEN}:start-action"), 409, "STATE_INVALID")  # 待处理
    err(await client.post(f"{API}/alerts/{OTHERS}:start-action"), 403, "NOT_ASSIGNEE")
    err(await client.post(f"{API}/alerts/{MINE_LOCKED}:start-action"), 409, "STATE_INVALID")  # 已有动作
    await client.post(f"{API}/alerts/{OPEN}:claim")
    fakes.checker = FailingChecker([{"name": "价格区间", "fail": "降幅超过 10%"}])
    b = err(await client.post(f"{API}/alerts/{OPEN}:start-action"), 422, "SUBMISSION_CRITERIA_FAILED")
    assert b["details"]["failed"] == [{"name": "价格区间", "fail": "降幅超过 10%"}]
    # 回滚后告警仍无关联动作
    assert (await client.get(f"{API}/alerts/{OPEN}")).json()["actionId"] is None
    # 规则没绑定动作
    await dyn.execute(update(Alert).where(Alert.code == OPEN_ORDER).values(rule_code="R-021"))
    await client.post(f"{API}/alerts/{OPEN_ORDER}:claim")
    b = err(await client.post(f"{API}/alerts/{OPEN_ORDER}:start-action"), 409, "STATE_INVALID")
    assert b["message"] == "规则未绑定动作"
    assert await dyn.scalar(select(Alert.action_run_id).where(Alert.code == OPEN_ORDER)) is None


async def test_page_params_come_from_skeleton(dyn, client):  # noqa: F811
    """页码交给骨架 PageParams：默认条数照前端（12 / 16），size 照传；page ≤ 0 不由 dynamic 决定——
    骨架收拢（9c3e506 起）→ 第 1 页；更早的骨架 PageParams 拒绝 → 422 VALIDATION_FAILED。两种都不能是 500。"""
    assert (await client.get(f"{API}/alerts")).json()["size"] == 12
    assert (await client.get(f"{API}/action-runs")).json()["size"] == 16
    assert (await client.get(f"{API}/alerts", params={"size": 5})).json()["size"] == 5
    for path in ("/alerts", "/action-runs"):
        r = await client.get(f"{API}{path}", params={"page": 0})
        if r.status_code == 200:
            assert r.json()["page"] == 1
        else:
            assert err(r, 422, "VALIDATION_FAILED")["details"]["fields"][0]["field"] == "page"
