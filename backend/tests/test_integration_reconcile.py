"""Durable reconciliation uses real rows; query responses are explicit injected test adapters."""

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.contracts.actions import ExtStatus, KeyQueryResult
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.action.models import ActionType, TypeStatus
from app.modules.dynamic.catalog import get_list_catalog
from app.modules.dynamic.models import ActionRun, ActionStatus, SourceType
from app.modules.dynamic.ports import Ports, UnconfiguredAdapters
from app.modules.dynamic.service import DynamicReconciliationListener
from app.modules.event.models import Event, EventPayloadField, EventSource, EventType
from app.modules.integration.adapters import get_adapter
from app.modules.integration.emitter import DatabaseEventEmitter
from app.modules.integration.models import (
    ExtSystem,
    ExtSystemStatus,
    ReconcileState,
    ReconcileTask,
    WritebackCall,
)
from app.modules.integration.reconcile import (
    INTERVAL,
    DatabaseReconcileScheduler,
    discover,
    lock_run,
    process,
)
from tests.test_use_ops_runtime import runtime  # noqa: F401
from tests.use_ops_support import grant_ops_roles


@pytest.fixture
async def reconciliation(session):
    await grant_ops_roles(session, {"U010001": "R-01"})
    system = ExtSystem(
        code="EXT-RECON-TEST",
        name="对账测试",
        timeout_ms=1000,
        idempotent=True,
        endpoint="https://reconcile.invalid",
        status=ExtSystemStatus.ok,
        env="测试",
    )
    action = ActionType(
        code="recon_action",
        name="对账动作",
        api="ReconAction",
        grp="测试",
        status=TypeStatus.published,
        version="v1",
    )
    session.add_all([system, action])
    await session.flush()
    action.sys_id = system.id
    run = ActionRun(
        code="ACT-RECON-TEST",
        action_type_code=action.code,
        action_name=action.name,
        type_version="v1",
        source_type=SourceType.manual,
        source_label="手动",
        initiator="测试",
        idem_key="recon-test-key",
        status=ActionStatus.unknown,
        sys_code=system.code,
        submitted_at=clock.now(),
        obj_id="actual-target",
        obj_label="目标",
        created_by="U010001",
    )
    event_type = EventType(
        code="action_executed",
        name="动作完成",
        api="ActionExecuted",
        source=EventSource.action,
        status=TypeStatus.published,
        version="v1",
        retention_days=30,
        object_api="ActionRun",
        dedup_key_rule="执行记录 ID",
        dedup_window="24 小时",
    )
    event_type.payload = [
        EventPayloadField(seq=0, api="runId", name="执行记录", type="字符串", required=True)
    ]
    session.add_all([run, event_type])
    await session.flush()
    return session, system, run


class Query:
    def __init__(self, status=ExtStatus.not_found, error=None):
        self.status, self.error, self.calls = status, error, []
        self.last_http = 200

    def path(self, operation):
        return "/idempotency-query"

    async def query_by_key(self, key):
        self.calls.append(key)
        if self.error:
            raise self.error
        return KeyQueryResult(self.status, "external-actual-receipt")


async def listener(session):
    unavailable = UnconfiguredAdapters()
    return DynamicReconciliationListener(
        session,
        await get_list_catalog(session),
        Ports(unavailable, DatabaseEventEmitter(session), DatabaseReconcileScheduler(session), unavailable),
    )


async def due(session, run):
    task = await DatabaseReconcileScheduler(session).ensure(await lock_run(session, run.code))
    task.next_at = clock.now()
    await session.flush()
    return task


async def test_three_queries_then_manual_and_real_reopen(reconciliation, monkeypatch):
    session, _, run = reconciliation
    task = await due(session, run)
    query = Query()
    lis = await listener(session)
    for count in range(1, 4):
        task.next_at = clock.now()
        await session.flush()
        await process(session, run.code, lis, lambda _: query)
        assert task.tries == count and run.status == ActionStatus.unknown
        if count < 3:
            assert task.state == ReconcileState.querying and task.next_at == clock.now() + INTERVAL
    assert task.state == ReconcileState.manual and task.manual and task.next_at is None
    assert query.calls == [run.idem_key] * 3
    assert await session.scalar(select(func.count()).select_from(WritebackCall)) == 3
    monkeypatch.setattr(get_settings(), "integration_real_connect", False)
    with pytest.raises(AppError) as exc:
        await DatabaseReconcileScheduler(session).reopen_reconcile(run.code)
    assert exc.value.status_code == 503 and task.round == 1 and task.tries == 3
    monkeypatch.setattr(get_settings(), "integration_real_connect", True)
    monkeypatch.setattr(
        get_settings(), "integration_adapters", SecretStr('{"EXT-RECON-TEST":{"queryPath":"/query"}}')
    )
    await DatabaseReconcileScheduler(session).reopen_reconcile(run.code)
    assert task.round == 2 and task.tries == 0 and task.state == ReconcileState.querying


@pytest.mark.parametrize("outcome", [ExtStatus.found_succeeded, ExtStatus.found_not_executed])
async def test_atomic_callback_and_no_duplicate_query(reconciliation, outcome):
    session, _, run = reconciliation
    await due(session, run)
    query = Query(outcome)
    lis = await listener(session)
    task = await process(session, run.code, lis, lambda _: query)
    assert task.state == ReconcileState.resolved and task.reconciler == "系统"
    assert run.status == ActionStatus.reconciled and run.reconcile_result.name == outcome.outcome.value
    events = list(await session.scalars(select(Event)))
    assert len(events) == (1 if outcome == ExtStatus.found_succeeded else 0)
    if events:
        assert events[0].outcome.value == "待投递" and events[0].payload["runId"] == run.code
    await process(session, run.code, lis, lambda _: query)
    assert query.calls == [run.idem_key]
    assert await session.scalar(select(func.count()).select_from(WritebackCall)) == 1


async def test_unconfigured_does_not_become_not_found_or_consume_try(reconciliation, monkeypatch):
    session, _, run = reconciliation
    await due(session, run)
    monkeypatch.setattr(get_settings(), "integration_real_connect", False)
    task = await process(session, run.code, await listener(session))
    assert task.state == ReconcileState.manual and task.last_error == "RECONCILE_UNAVAILABLE"
    assert task.tries == 0 and task.last_ext_status is None and run.status == ActionStatus.unknown
    assert await session.scalar(select(func.count()).select_from(WritebackCall)) == 0


async def test_callback_failure_preserves_unknown_and_real_attempt(reconciliation):
    session, _, run = reconciliation
    await due(session, run)

    class FailingListener:
        async def on_reconciled(self, code, result):
            run.status = ActionStatus.reconciled
            await session.flush()
            raise AppError("真实事件定义不可用", code="EVENT_CONTEXT_UNAVAILABLE", status_code=503)

    task = await process(session, run.code, FailingListener(), lambda _: Query(ExtStatus.found_succeeded))
    await session.refresh(run)
    assert run.status == ActionStatus.unknown and task.state == ReconcileState.manual
    assert task.last_error == "CALLBACK_EVENT_CONTEXT_UNAVAILABLE" and task.tries == 1
    assert await session.scalar(select(func.count()).select_from(WritebackCall)) == 1


async def test_restart_discovery_and_non_idempotent_stay_manual(reconciliation):
    session, system, run = reconciliation
    run.is_seed = True
    await session.flush()
    await discover(session)
    task = await session.scalar(select(ReconcileTask))
    assert task.state == ReconcileState.manual and task.tries == 3 and task.next_at is None
    system.idempotent = False
    await session.flush()
    await DatabaseReconcileScheduler(session).reopen_reconcile(run.code)
    assert (
        task.round == 2 and task.tries == 0 and task.next_at is None and task.state == ReconcileState.manual
    )
    await discover(session)
    assert await session.scalar(select(func.count()).select_from(ReconcileTask)) == 1


async def test_http_adapter_requires_real_explicit_response(reconciliation, monkeypatch):
    import httpx

    _, system, _ = reconciliation
    settings = get_settings()
    monkeypatch.setattr(settings, "integration_real_connect", True)
    monkeypatch.setattr(
        settings,
        "integration_adapters",
        SecretStr('{"EXT-RECON-TEST":{"queryPath":"/query","token":"not-a-real-secret"}}'),
    )
    responses = iter(
        [
            httpx.Response(200, json={"extStatus": "查到·成功", "extRef": "OK"}),
            httpx.Response(404),
            httpx.Response(200, json={"status": "ok"}),
        ]
    )

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params, headers):
            assert url == "https://reconcile.invalid/query" and params == {"idemKey": "key"}
            assert headers["Authorization"] == "Bearer not-a-real-secret"
            return next(responses)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    adapter = get_adapter(system)
    assert (await adapter.query_by_key("key")).ext_status == ExtStatus.found_succeeded
    for code in ("EXT_QUERY_FAILED", "EXT_QUERY_INVALID_RESPONSE"):
        with pytest.raises(AppError) as error:
            await adapter.query_by_key("key")
        assert error.value.code == code and error.value.status_code == 503


async def test_no_auth_probe_and_query_ignore_configured_token(reconciliation, monkeypatch):
    import httpx

    _, system, _ = reconciliation
    system.auth_type = "无需授权"
    settings = get_settings()
    monkeypatch.setattr(settings, "integration_real_connect", True)
    monkeypatch.setattr(
        settings,
        "integration_adapters",
        SecretStr('{"EXT-RECON-TEST":{"queryPath":"/query","probePath":"/probe","token":"stale-token"}}'),
    )
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params, headers):
            calls.append((url, headers))
            return httpx.Response(200, json={"extStatus": "查到·成功", "extRef": "OK"})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    adapter = get_adapter(system)
    await adapter.query_by_key("key")
    await adapter.request("probe")
    assert len(calls) == 2 and all("Authorization" not in headers for _, headers in calls)
    system.auth_type = "Token（服务账号）"
    await get_adapter(system).request("probe")
    assert calls[-1][1]["Authorization"] == "Bearer stale-token"
    monkeypatch.setattr(
        settings, "integration_adapters", SecretStr('{"EXT-RECON-TEST":{"probePath":"/probe"}}')
    )
    await get_adapter(system, "probe").request("probe")
    assert "Authorization" not in calls[-1][1]
    system.auth_type = "无需授权"
    await get_adapter(system, "probe").request("probe")
    assert "Authorization" not in calls[-1][1]


async def test_manual_resolution_closes_task_in_same_transaction(runtime):  # noqa: F811
    from app.common.auth import resolve_user
    from app.modules.dynamic.service import ActionService

    session, action, _ = runtime
    system = ExtSystem(
        code="OPS-MANUAL",
        name="人工对账测试",
        timeout_ms=1000,
        idempotent=True,
        endpoint="https://manual.invalid",
        status=ExtSystemStatus.ok,
        env="测试",
    )
    session.add(system)
    await session.flush()
    action.sys_id = system.id
    run = await session.scalar(select(ActionRun).where(ActionRun.code == "OPS-R00"))
    run.status, run.sys_code = ActionStatus.unknown, system.code
    await session.flush()
    task = await due(session, run)
    unavailable = UnconfiguredAdapters()
    scheduler = DatabaseReconcileScheduler(session)
    actor = await resolve_user(session, "U010001")
    service = ActionService(
        session,
        actor,
        await get_list_catalog(session),
        Ports(unavailable, DatabaseEventEmitter(session), scheduler, unavailable),
    )
    await service.reconcile(run.code, "外部未执行", "真实人工核查")
    assert task.state == ReconcileState.resolved and task.reconciler == actor.name
    query = Query()
    await process(session, run.code, await listener(session), lambda _: query)
    assert query.calls == [] and run.status == ActionStatus.reconciled


async def test_configured_probe_persists_real_result(reconciliation, monkeypatch):
    import httpx

    from app.modules.integration.service import ExtSystemService

    session, system, _ = reconciliation
    monkeypatch.setattr(get_settings(), "integration_real_connect", True)
    monkeypatch.setattr(
        get_settings(), "integration_adapters", SecretStr('{"EXT-RECON-TEST":{"probePath":"/health"}}')
    )
    statuses = iter([204, 503])

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params, headers):
            assert url == "https://reconcile.invalid/health" and params is None
            return httpx.Response(next(statuses))

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    svc = ExtSystemService(session)
    for result, status in (("成功", 204), ("失败", 503)):
        row = await svc.test_call(system.code, "测试人")
        assert row["result"] == result and row["http"] == status and row["ms"] >= 0
        assert await session.scalar(select(WritebackCall.id).where(WritebackCall.code == row["id"]))
