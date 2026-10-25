"""动作中心：暂存 / 确认 / 撤回 / 审批（三种结果）/ 重新提交 / 人工对账 / 自动对账回调。"""

from app.contracts.actions import ReconcileOutcome, ReconcileResult, SubmitResult, SubmitStatus
from app.modules.dynamic import ports
from app.modules.dynamic.catalog import get_catalog
from app.modules.dynamic.service import DynamicReconciliationListener
from tests.dynamic_support import API, FailingChecker, FakeSubmitter, as_, dyn, err, fakes  # noqa: F401

STAGED_OTHER = "ACT-20261025-0552"  # 待确认，确认人 周晓峰
STAGED_MINE = "ACT-20261025-0548"  # 待确认，问数，王敏
STAGED_ALERT = "ACT-20261025-0550"  # 待确认，告警 AL-20261025-0127，王敏
APPROVING = "ACT-20261025-0551"
EXECUTING = "ACT-20261025-0542"
FAILED = "ACT-20261025-0545"
UNKNOWN = "ACT-20261025-0543"
UNKNOWN2 = "ACT-20261025-0544"

MANUAL = {
    "action": "补货申请",
    "source": {"type": "手动"},
    "objKind": "product",
    "objId": "1004512",
    "objLabel": "x",
}


def texts(d):
    return [x["text"] for x in d["log"]]


async def test_list_filters(dyn, client):  # noqa: F811
    g = lambda **p: client.get(f"{API}/action-runs", params=p)  # noqa: E731
    r = (await g(tab="pending", status="待确认", size=50)).json()
    assert r["total"] == 25 and {x["status"] for x in r["items"]} == {"待确认"}
    assert r["statusCounts"]["待确认"] == 25
    r = (await g(tab="unknown", status="结果未知", size=50)).json()
    assert r["total"] == 20
    r = (await g(scope="center", actionType="request_replenish", size=200)).json()
    assert r["total"] > 0 and all(x["typeId"] == "request_replenish" for x in r["items"])
    r = (await g(scope="center", alertId="AL-20261025-0127")).json()
    assert [x["id"] for x in r["items"]] == [STAGED_ALERT]
    r = (await g(scope="center", kw="0552")).json()
    assert [x["id"] for x in r["items"]] == [STAGED_OTHER]
    sess = (await client.get(f"{API}/action-runs/{STAGED_MINE}")).json()["source"]["session"]
    r = (await g(scope="center", sessionId=sess)).json()
    assert STAGED_MINE in [x["id"] for x in r["items"]]
    err(await g(tab="nope"), 422, "VALIDATION_FAILED")
    err(await g(scope="nope"), 422, "VALIDATION_FAILED")
    err(await g(size=201), 422, "VALIDATION_FAILED")
    err(await client.get(f"{API}/action-runs/ACT-NOPE"), 404, "NOT_FOUND")


async def test_stage_sources(dyn, client, fakes):  # noqa: F811
    r = await client.post(f"{API}/action-runs:stage", json=MANUAL)
    assert r.status_code == 201, r.text
    d = r.json()
    # confirmer 只存 source.confirmer（手动/问数没有 → 不限操作人，Actions.vue L87）
    assert d["status"] == "待确认" and d["initiator"] == "王敏" and d["confirmer"] is None
    assert texts(d) == ["手动发起", "生成动作「补货申请」· 动作类型 v3", "等待确认提交"]
    assert d["idemKey"] == d["id"].lower()
    assert fakes.submitter.calls == []  # 暂存不调外部
    qa = {**MANUAL, "source": {"type": "问数", "session": "QA-1", "sessionTitle": "补货"}}
    d = (await client.post(f"{API}/action-runs:stage", json=qa)).json()
    assert texts(d)[0] == "问数会话 QA-1「补货」建议" and d["source"]["session"] == "QA-1"
    rule = {**MANUAL, "action": "发起物流催派", "source": {"type": "规则", "ruleId": "R-015"}}
    d = (await client.post(f"{API}/action-runs:stage", json=rule)).json()
    assert d["initiator"] == "规则 R-015" and d["confirmer"] == "陈立军"
    sim = {**MANUAL, "source": {"type": "推演", "scenarioId": "SC-1", "scenarioName": "促销"}}
    d = (await client.post(f"{API}/action-runs:stage", json=sim)).json()
    assert texts(d)[0] == "情景推演 SC-1「促销」选定方案，转为暂存动作"
    by_type = {**MANUAL, "action": None, "typeId": "request_replenish"}
    assert (await client.post(f"{API}/action-runs:stage", json=by_type)).json()["action"] == "补货申请"
    mismatch = {**MANUAL, "typeId": "urge_logistics"}
    err(await client.post(f"{API}/action-runs:stage", json=mismatch), 422, "VALIDATION_FAILED")
    err(
        await client.post(f"{API}/action-runs:stage", json={**MANUAL, "action": "不存在的动作"}),
        422,
        "VALIDATION_FAILED",
    )
    err(
        await client.post(f"{API}/action-runs:stage", json={**MANUAL, "action": None}),
        422,
        "VALIDATION_FAILED",
    )
    bad_alert = {**MANUAL, "source": {"type": "告警", "alertId": "AL-X"}}
    err(await client.post(f"{API}/action-runs:stage", json=bad_alert), 422, "VALIDATION_FAILED")
    fakes.checker = FailingChecker([{"name": "库存", "fail": "不足"}])
    err(await client.post(f"{API}/action-runs:stage", json=MANUAL), 422, "SUBMISSION_CRITERIA_FAILED")


async def test_stage_idempotency(dyn, client, fakes):  # noqa: F811
    seeded = (await client.get(f"{API}/action-runs/{STAGED_ALERT}")).json()
    body = {
        "action": seeded["action"],
        "source": {"type": "告警", "alertId": "AL-20261025-0127"},
        "objKind": seeded["objKind"],
        "objId": seeded["objId"],
        "objLabel": seeded["objLabel"],
        "params": seeded["params"],
    }
    r = await client.post(f"{API}/action-runs:stage", json=body)
    assert r.status_code == 200, r.text  # 重放：返回原记录
    assert r.json()["id"] == STAGED_ALERT
    b = err(
        await client.post(f"{API}/action-runs:stage", json={**body, "objId": "999"}),
        409,
        "IDEMPOTENCY_CONFLICT",
    )
    assert b["details"] == {"idemKey": "act-al-20261025-0127", "id": STAGED_ALERT}


async def test_confirm_withdraw(dyn, client, fakes):  # noqa: F811
    b = err(await client.post(f"{API}/action-runs/{STAGED_OTHER}:confirm"), 403, "NOT_CONFIRMER")
    assert "周晓峰" in b["message"]
    err(
        await client.post(f"{API}/action-runs/{STAGED_OTHER}:withdraw", json={"reason": "x"}),
        403,
        "NOT_CONFIRMER",
    )
    rule = {**MANUAL, "action": "发起物流催派", "source": {"type": "规则", "ruleId": "R-015"}}
    mine = (await client.post(f"{API}/action-runs:stage", json=rule)).json()["id"]  # 确认人 陈立军
    err(await client.post(f"{API}/action-runs/{mine}:confirm"), 403, "NOT_CONFIRMER")
    chen = await as_(dyn, "陈立军")
    d = (await client.post(f"{API}/action-runs/{mine}:confirm", headers=chen)).json()
    assert d["status"] == "审批中" and texts(d)[-2:] == ["确认提交", "等待审批"]
    err(await client.post(f"{API}/action-runs/{mine}:confirm", headers=chen), 409, "STATE_INVALID")
    d = (await client.post(f"{API}/action-runs/{STAGED_MINE}:withdraw", json={"reason": "不需要了"})).json()
    assert d["status"] == "已撤回" and texts(d)[-1] == "撤回：不需要了"
    err(
        await client.post(f"{API}/action-runs/{STAGED_MINE}:withdraw", json={"reason": "x"}),
        409,
        "STATE_INVALID",
    )
    err(
        await client.post(f"{API}/action-runs/{STAGED_ALERT}:withdraw", json={"reason": ""}),
        422,
        "VALIDATION_FAILED",
    )
    fakes.checker = FailingChecker([{"name": "价格", "fail": "越界"}])
    err(await client.post(f"{API}/action-runs/{STAGED_ALERT}:confirm"), 422, "SUBMISSION_CRITERIA_FAILED")
    assert (await client.get(f"{API}/action-runs/{STAGED_ALERT}")).json()["status"] == "待确认"


async def test_approve_success(dyn, client, fakes):  # noqa: F811
    d = (await client.post(f"{API}/action-runs/{APPROVING}:approve")).json()
    assert d["status"] == "成功" and d["extRef"] == "OA-1"
    assert texts(d)[-3] == "审批通过" and texts(d)[-1].endswith("返回成功，外部单号 OA-1")
    (eid, typ, payload) = fakes.emitter.events[0]
    assert (
        typ == "ActionExecuted"
        and payload["actionRunId"] == APPROVING
        and eid.startswith("dynamic:action_run:")
    )
    err(await client.post(f"{API}/action-runs/{APPROVING}:approve"), 409, "STATE_INVALID")
    err(await client.post(f"{API}/action-runs/{STAGED_MINE}:approve"), 409, "STATE_INVALID")


async def test_approve_failed_then_resubmit(dyn, client, fakes):  # noqa: F811
    fakes.submitter = FakeSubmitter(
        SubmitResult(status=SubmitStatus.FAILED, reason="HTTP 422 价格越界", code="422", retries=2)
    )
    d = (await client.post(f"{API}/action-runs/{APPROVING}:approve")).json()
    assert d["status"] == "失败" and d["failReason"] == "HTTP 422 价格越界" and d["retries"] == 2
    assert texts(d)[-1].endswith("明确返回失败，已自动重试 2 次仍失败")
    assert fakes.emitter.events == []
    fakes.submitter = FakeSubmitter(SubmitResult(status=SubmitStatus.SUCCESS, receipt_id="OA-2"))
    r = (await client.post(f"{API}/action-runs/{APPROVING}:resubmit")).json()
    assert r["idemKey"] == f"{d['idemKey']}-r1" and r["action"]["status"] == "成功"
    assert r["action"]["retryOf"] == APPROVING and r["original"]["retriedAs"] == r["id"]
    assert fakes.submitter.calls == [(r["id"], r["idemKey"])]
    assert texts(r["original"])[-1] == f"已重新提交为 {r['id']}"
    b = err(await client.post(f"{API}/action-runs/{APPROVING}:resubmit"), 409, "ALREADY_RESUBMITTED")
    assert b["details"] == {"retriedAs": r["id"]}
    err(await client.post(f"{API}/action-runs/{EXECUTING}:resubmit"), 409, "STATE_INVALID")


async def test_resubmit_seed_failed_and_alert(dyn, client, fakes):  # noqa: F811
    r = (await client.post(f"{API}/action-runs/{FAILED}:resubmit")).json()
    assert r["action"]["status"] == "成功" and r["action"]["approver"] == r["original"]["approver"]
    # 告警上挂的失败动作重新提交：告警改指向新动作，新动作成功 → 告警转已解决
    a = (await client.get(f"{API}/alerts/AL-20261024-0118")).json()
    assert a["actionStatus"] == "失败"
    r = (await client.post(f"{API}/action-runs/{a['actionId']}:resubmit")).json()
    a = (await client.get(f"{API}/alerts/AL-20261024-0118")).json()
    assert a["actionId"] == r["id"] and a["status"] == "已解决"


async def test_approve_unknown_and_reconcile(dyn, client, fakes):  # noqa: F811
    fakes.submitter = FakeSubmitter(SubmitResult(status=SubmitStatus.UNKNOWN, reason="读超时"))
    d = (await client.post(f"{API}/action-runs/{APPROVING}:approve")).json()
    assert d["status"] == "结果未知" and d["unknownReason"] == "读超时"
    assert "秒未返回结果，标记为结果未知（不重试写入）" in texts(d)[-1]
    assert [e[1] for e in fakes.emitter.events] == ["ActionOutcomeUnknown"]
    # 仍无法确认：保持结果未知，重新排对账
    d = (
        await client.post(
            f"{API}/action-runs/{APPROVING}:reconcile", json={"result": "仍无法确认", "note": "OA 没查到"}
        )
    ).json()
    assert d["status"] == "结果未知" and fakes.scheduler.reopened == [APPROVING]
    d = (
        await client.post(
            f"{API}/action-runs/{APPROVING}:reconcile", json={"result": "外部已成功", "note": "查到了"}
        )
    ).json()
    assert d["status"] == "已对账" and d["reconcile"] == "外部已成功"
    assert texts(d)[-1] == "对账：外部已成功（查到了）"
    assert [e[1] for e in fakes.emitter.events] == ["ActionOutcomeUnknown", "ActionExecuted"]
    err(
        await client.post(
            f"{API}/action-runs/{APPROVING}:reconcile", json={"result": "外部已成功", "note": "x"}
        ),
        409,
        "STATE_INVALID",
    )
    # 外部未执行 → 已对账，不能重新提交
    d = (
        await client.post(
            f"{API}/action-runs/{UNKNOWN}:reconcile", json={"result": "外部未执行", "note": "无记录"}
        )
    ).json()
    assert d["reconcile"] == "外部未执行" and texts(d)[-1] == "对账：外部未执行，动作未生效（无记录）"
    b = err(await client.post(f"{API}/action-runs/{UNKNOWN}:resubmit"), 409, "STATE_INVALID")
    assert "外部未执行" in b["message"]
    err(
        await client.post(f"{API}/action-runs/{UNKNOWN2}:reconcile", json={"result": "瞎填", "note": "x"}),
        422,
        "VALIDATION_FAILED",
    )
    err(
        await client.post(
            f"{API}/action-runs/{UNKNOWN2}:reconcile", json={"result": "外部未执行", "note": " "}
        ),
        422,
        "VALIDATION_FAILED",
    )


async def test_on_reconciled_listener(dyn):  # noqa: F811
    em = ports.RecordingEmitter()
    p = ports.Ports(ports.StubSubmitter(), em, ports.RecordingScheduler(), ports.StubSubmissionChecker())
    lis = DynamicReconciliationListener(dyn, get_catalog(), p)
    await lis.on_reconciled(UNKNOWN, ReconcileResult(outcome=ReconcileOutcome.succeeded, receipt_id="OA-9"))
    await lis.on_reconciled(UNKNOWN2, ReconcileResult(outcome=ReconcileOutcome.not_executed))
    from sqlalchemy import select

    from app.modules.dynamic.models import ActionRun

    rows = {
        r.code: r
        for r in (await dyn.scalars(select(ActionRun).where(ActionRun.code.in_([UNKNOWN, UNKNOWN2])))).all()
    }
    assert rows[UNKNOWN].status.value == "已对账" and rows[UNKNOWN].ext_ref == "OA-9"
    assert rows[UNKNOWN2].reconcile_result.value == "外部未执行"
    assert [e[1] for e in em.events] == ["ActionExecuted"] and em.events[0][2]["extRef"] == "OA-9"
    from app.core.errors import AppError

    for code in (UNKNOWN, "ACT-NOPE"):
        try:
            await lis.on_reconciled(code, ReconcileResult(outcome=ReconcileOutcome.succeeded))
        except AppError as e:
            assert e.code in ("STATE_INVALID", "NOT_FOUND")
        else:
            raise AssertionError(code)


async def test_readonly_and_stubs(dyn, client):  # noqa: F811
    for path, body in [
        (f"{API}/action-runs:stage", MANUAL),
        (f"{API}/action-runs/{STAGED_MINE}:confirm", None),
        (f"{API}/action-runs/{STAGED_MINE}:withdraw", {"reason": "x"}),
        (f"{API}/action-runs/{APPROVING}:approve", None),
        (f"{API}/action-runs/{FAILED}:resubmit", None),
        (f"{API}/action-runs/{UNKNOWN}:reconcile", {"result": "外部已成功", "note": "x"}),
    ]:
        err(await client.post(path, params={"ver": "v2026.10.1"}, json=body), 403, "READONLY")
    # 默认 StubSubmitter：审批后真实走成功分支
    d = (await client.post(f"{API}/action-runs/{APPROVING}:approve")).json()
    assert d["status"] == "成功" and d["extRef"]


async def test_history_keeps_runs_with_retired_or_missing_action_type(dyn, client):  # noqa: F811
    """动作类型是软引用（action_type_code，FK to add later）：
    类型下线 / 不在目录里，历史记录照样列出、详情照样能看。"""
    gone = "retired_gone_type"
    from sqlalchemy import func, select, update

    from app.modules.dynamic.models import ActionRun

    before = (await client.get(f"{API}/action-runs", params={"scope": "all", "size": 1})).json()["total"]
    code = await dyn.scalar(select(ActionRun.code).order_by(ActionRun.code).limit(1))
    await dyn.execute(
        update(ActionRun).where(ActionRun.code == code).values(action_type_code="retired_gone_type")
    )
    await dyn.commit()
    assert await dyn.scalar(select(func.count()).where(ActionRun.action_type_code == gone)) == 1
    after = (await client.get(f"{API}/action-runs", params={"scope": "all", "size": 200})).json()
    assert after["total"] == before
    if after["total"] <= 200:
        assert code in {x["id"] for x in after["items"]}
    d = await client.get(f"{API}/action-runs/{code}")
    assert d.status_code == 200, d.text
