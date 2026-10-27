"""Action transitions with actual current grants/structured definitions and explicit test ports.

Legacy retail summaries are no longer executable fixtures. Positive state tests use the
same bounded local definition as the real PG regression; only external outcomes are injected.
"""

import pytest
from sqlalchemy import select

from app.contracts.actions import ReconcileOutcome, ReconcileResult, SubmitResult, SubmitStatus
from app.main import app
from app.modules.dynamic import ports
from app.modules.dynamic.catalog import get_catalog
from app.modules.dynamic.models import ActionRun, ActionStatus, Alert, AlertStatus, SourceType
from app.modules.dynamic.service import DynamicReconciliationListener
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from tests.dynamic_support import API, FailingChecker, Fakes, FakeSubmitter, err
from tests.test_use_ops_runtime import headers, payload, runtime  # noqa: F401

STAGED_OTHER = "OPS-R02"
STAGED_MINE = "OPS-R00"
STAGED_ALERT = "OPS-R03"
APPROVING = "OPS-R01"
FAILED = "OPS-R04"
UNKNOWN = "OPS-R05"
UNKNOWN2 = "OPS-R06"


def texts(detail):
    return [row["text"] for row in detail["log"]]


@pytest.fixture
async def fakes(runtime, client):  # noqa: F811
    value = Fakes(
        FakeSubmitter(SubmitResult(status=SubmitStatus.SUCCESS, receipt_id="TEST-OA-1", code="200")),
        ports.RecordingEmitter(),
        ports.RecordingScheduler(),
        ports.StubSubmissionChecker(),
    )
    app.dependency_overrides[ports.get_submitter] = lambda: value.submitter
    app.dependency_overrides[ports.get_emitter] = lambda: value.emitter
    app.dependency_overrides[ports.get_scheduler] = lambda: value.scheduler
    app.dependency_overrides[ports.get_checker] = lambda: value.checker
    return value


async def record(session, code, status=None, **values):
    row = await session.scalar(select(ActionRun).where(ActionRun.code == code))
    if status is not None:
        row.status = status
    for key, value in values.items():
        setattr(row, key, value)
    await session.flush()
    return row


async def approver_h(client, db):
    await record(db, APPROVING, ActionStatus.approving, approver="U010001")
    return headers()


async def test_list_filters(runtime, client):  # noqa: F811
    get = lambda **params: client.get(f"{API}/action-runs", params=params)  # noqa: E731
    data = (await get(tab="pending", size=50)).json()
    assert data["total"] == 13 and data["statusCounts"]["待确认"] == 13
    assert {x["status"] for x in data["items"]} == {"待确认"}
    assert (await get(scope="center", actionType="ops_local")).json()["total"] == 13
    assert [x["id"] for x in (await get(scope="center", kw="OPS-R02")).json()["items"]] == [STAGED_OTHER]
    assert (await get(tab="unknown")).json()["total"] == 0
    err(await get(tab="nope"), 422, "VALIDATION_FAILED")
    err(await get(scope="nope"), 422, "VALIDATION_FAILED")
    err(await get(size=201), 422, "VALIDATION_FAILED")
    err(await client.get(f"{API}/action-runs/NOPE"), 404, "NOT_FOUND")


async def test_stage_sources(runtime, client, fakes):  # noqa: F811
    body = payload()
    response = await client.post(f"{API}/action-runs:stage", json=body)
    assert response.status_code == 201, response.text
    detail = response.json()
    assert detail["status"] == "待确认" and detail["confirmer"] == "王敏"
    assert texts(detail)[0] == "手动发起" and not fakes.submitter.calls
    for source in (
        {"type": "问数", "session": "QA-1"},
        {"type": "规则", "ruleId": "R-1"},
        {"type": "推演", "scenarioId": "SC-1"},
        {"type": "告警", "alertId": "OPS-A00"},
    ):
        err(
            await client.post(f"{API}/action-runs:stage", json={**payload(), "source": source}),
            422,
            "VALIDATION_FAILED",
        )
    err(
        await client.post(f"{API}/action-runs:stage", json={**payload(), "typeId": "NOPE"}),
        422,
        "VALIDATION_FAILED",
    )
    fakes.checker = FailingChecker([{"name": "显式条件", "fail": "不满足"}])
    err(await client.post(f"{API}/action-runs:stage", json=payload()), 422, "SUBMISSION_CRITERIA_FAILED")


async def test_stage_idempotency(runtime, client, fakes):  # noqa: F811
    body = payload()
    first = await client.post(f"{API}/action-runs:stage", json=body)
    again = await client.post(f"{API}/action-runs:stage", json=body)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    err(
        await client.post(f"{API}/action-runs:stage", json={**body, "objId": "OPS-S01"}),
        409,
        "IDEMPOTENCY_CONFLICT",
    )
    assert not fakes.submitter.calls


async def test_confirm_withdraw(runtime, client, fakes):  # noqa: F811
    session, action, _ = runtime
    action.approval = "U010001"
    await record(session, STAGED_OTHER, confirmer="OPS-LIMIT")
    err(await client.post(f"{API}/action-runs/{STAGED_OTHER}:confirm"), 403, "NOT_CONFIRMER")
    err(
        await client.post(f"{API}/action-runs/{STAGED_OTHER}:withdraw", json={"reason": "test"}),
        403,
        "NOT_CONFIRMER",
    )
    confirmed = await client.post(f"{API}/action-runs/{STAGED_MINE}:confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "审批中" and texts(confirmed.json())[-1] == "等待审批"
    err(await client.post(f"{API}/action-runs/{STAGED_MINE}:confirm"), 409, "STATE_INVALID")
    withdrawn = await client.post(f"{API}/action-runs/{STAGED_ALERT}:withdraw", json={"reason": "不需要"})
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "已撤回"
    err(
        await client.post(f"{API}/action-runs/{STAGED_ALERT}:withdraw", json={"reason": "test"}),
        409,
        "STATE_INVALID",
    )
    fakes.checker = FailingChecker([{"name": "条件", "fail": "不满足"}])
    err(await client.post(f"{API}/action-runs/{UNKNOWN}:confirm"), 422, "SUBMISSION_CRITERIA_FAILED")
    assert (await client.get(f"{API}/action-runs/{UNKNOWN}")).json()["status"] == "待确认"


async def test_approve_success(runtime, client, fakes):  # noqa: F811
    h = await approver_h(client, runtime[0])
    response = await client.post(f"{API}/action-runs/{APPROVING}:approve", headers=h)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "成功" and response.json()["extRef"] == "TEST-OA-1"
    assert [e[1] for e in fakes.emitter.events] == ["ActionExecuted"]
    err(await client.post(f"{API}/action-runs/{APPROVING}:approve", headers=h), 409, "STATE_INVALID")


async def test_approve_failed_then_resubmit(runtime, client, fakes):  # noqa: F811
    h = await approver_h(client, runtime[0])
    fakes.submitter = FakeSubmitter(
        SubmitResult(SubmitStatus.FAILED, reason="测试网关明确拒绝", code="422", retries=2)
    )
    failed = await client.post(f"{API}/action-runs/{APPROVING}:approve", headers=h)
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "失败" and failed.json()["retries"] == 2
    assert not fakes.emitter.events
    fakes.submitter = FakeSubmitter(SubmitResult(SubmitStatus.SUCCESS, receipt_id="TEST-OA-2"))
    result = await client.post(f"{API}/action-runs/{APPROVING}:resubmit")
    assert result.status_code == 200, result.text
    assert result.json()["idemKey"] == failed.json()["idemKey"] + "-r1"
    assert result.json()["action"]["retryOf"] == APPROVING
    assert result.json()["original"]["retriedAs"] == result.json()["id"]
    err(await client.post(f"{API}/action-runs/{APPROVING}:resubmit"), 409, "ALREADY_RESUBMITTED")


async def test_resubmit_seed_failed_and_alert(runtime, client, fakes):  # noqa: F811
    session = runtime[0]
    row = await record(session, FAILED, ActionStatus.failed, is_seed=True)
    alert = await session.scalar(select(Alert).where(Alert.code == "OPS-A04"))
    alert.status, alert.assignee, alert.action_run_id = AlertStatus.processing, "U010001", row.id
    row.alert_id, row.source_type = alert.id, SourceType.alert
    await session.flush()
    result = await client.post(f"{API}/action-runs/{FAILED}:resubmit")
    assert result.status_code == 200, result.text
    assert result.json()["action"]["status"] == "成功"
    detail = (await client.get(f"{API}/alerts/OPS-A04")).json()
    assert detail["actionId"] == result.json()["id"] and detail["status"] == "已解决"


async def test_approve_unknown_and_reconcile(runtime, client, fakes):  # noqa: F811
    await approver_h(client, runtime[0])
    fakes.submitter = FakeSubmitter(SubmitResult(SubmitStatus.UNKNOWN, reason="测试网关读超时"))
    unknown = await client.post(f"{API}/action-runs/{APPROVING}:approve")
    assert unknown.status_code == 200 and unknown.json()["status"] == "结果未知", unknown.text
    assert [e[1] for e in fakes.emitter.events] == ["ActionOutcomeUnknown"]
    response = await client.post(
        f"{API}/action-runs/{APPROVING}:reconcile", json={"result": "仍无法确认", "note": "test"}
    )
    assert response.status_code == 200 and response.json()["status"] == "结果未知"
    assert fakes.scheduler.reopened == [APPROVING]
    response = await client.post(
        f"{API}/action-runs/{APPROVING}:reconcile", json={"result": "外部已成功", "note": "测试适配器核对"}
    )
    assert response.status_code == 200 and response.json()["reconcile"] == "外部已成功"
    assert [e[1] for e in fakes.emitter.events] == ["ActionOutcomeUnknown", "ActionExecuted"]
    err(
        await client.post(
            f"{API}/action-runs/{APPROVING}:reconcile", json={"result": "外部已成功", "note": "test"}
        ),
        409,
        "STATE_INVALID",
    )
    await record(runtime[0], UNKNOWN, ActionStatus.unknown)
    response = await client.post(
        f"{API}/action-runs/{UNKNOWN}:reconcile", json={"result": "外部未执行", "note": "test"}
    )
    assert response.status_code == 200 and response.json()["reconcile"] == "外部未执行"
    err(await client.post(f"{API}/action-runs/{UNKNOWN}:resubmit"), 409, "STATE_INVALID")
    err(
        await client.post(f"{API}/action-runs/{UNKNOWN2}:reconcile", json={"result": "瞎填", "note": "test"}),
        422,
        "VALIDATION_FAILED",
    )


async def test_on_reconciled_listener(runtime):  # noqa: F811
    from app.common.auth import resolve_user

    session = runtime[0]
    for code in (UNKNOWN, UNKNOWN2):
        await record(session, code, ActionStatus.unknown)
    emitted = ports.RecordingEmitter()
    listener = DynamicReconciliationListener(
        session,
        await get_catalog(session, await resolve_user(session, "U010001")),
        ports.Ports(
            ports.StubSubmitter(), emitted, ports.RecordingScheduler(), ports.StubSubmissionChecker()
        ),
    )
    await listener.on_reconciled(UNKNOWN, ReconcileResult(ReconcileOutcome.succeeded, "TEST-RECEIPT"))
    await listener.on_reconciled(UNKNOWN2, ReconcileResult(ReconcileOutcome.not_executed))
    await listener.on_reconciled(UNKNOWN, ReconcileResult(ReconcileOutcome.succeeded, "TEST-RECEIPT"))
    assert len(emitted.events) == 1
    assert (
        await session.scalar(select(ActionRun).where(ActionRun.code == UNKNOWN))
    ).status == ActionStatus.reconciled


async def test_readonly_and_stubs(runtime, client):  # noqa: F811
    await approver_h(client, runtime[0])
    for path, body in (
        ("action-runs:stage", payload()),
        (f"action-runs/{STAGED_MINE}:confirm", None),
        (f"action-runs/{STAGED_MINE}:withdraw", {"reason": "test"}),
        (f"action-runs/{APPROVING}:approve", None),
        (f"action-runs/{FAILED}:resubmit", None),
        (f"action-runs/{UNKNOWN}:reconcile", {"result": "外部未执行", "note": "test"}),
    ):
        err(await client.post(f"{API}/{path}", params={"ver": "v2026.10.1"}, json=body), 403, "READONLY")
    session, action, _ = runtime
    system = ExtSystem(
        code="OPS-UNCONFIGURED",
        name="未配置测试通道",
        endpoint="https://unconfigured.invalid",
        timeout_ms=1000,
        idempotent=True,
        idem_header="Idempotency-Key",
        status=ExtSystemStatus.ok,
        env="test",
    )
    session.add(system)
    await session.flush()
    action.sys_id = system.id
    await record(session, APPROVING, sys_code=system.code)
    response = await client.post(f"{API}/action-runs/{APPROVING}:approve")
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "ACTION_EXECUTOR_UNAVAILABLE"
    assert (await client.get(f"{API}/action-runs/{APPROVING}")).json()["status"] == "审批中"


async def test_history_keeps_runs_with_retired_or_missing_action_type(runtime, client):  # noqa: F811
    session = runtime[0]
    before = (await client.get(f"{API}/action-runs?scope=all")).json()["total"]
    await record(session, STAGED_MINE, action_type_code="retired-gone")
    response = await client.get(f"{API}/action-runs/{STAGED_MINE}")
    assert response.status_code == 200 and response.json()["paramsUnavailable"]
    assert (await client.get(f"{API}/action-runs?scope=all")).json()["total"] == before


async def test_approve_only_approver(runtime, client):  # noqa: F811
    await approver_h(client, runtime[0])
    other = headers("OPS-LIMIT")
    detail = (await client.get(f"{API}/action-runs/{APPROVING}", headers=other)).json()
    assert detail["can"]["approve"] is False and "只有审批人" in detail["can"]["reasons"]["approve"]
    err(await client.post(f"{API}/action-runs/{APPROVING}:approve", headers=other), 403, "NOT_APPROVER")
