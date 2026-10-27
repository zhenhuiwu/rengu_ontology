"""Current PG authority and source checks for safe requeue; no network send in these requests."""

import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.common.auth import resolve_user
from app.common.outbox import emit
from app.core import clock
from app.modules.action.models import EffectKind, SideEffect
from app.modules.admin.models import AuditLog, SysRole
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.integration import action_effects, adapters
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from tests.test_use_ops_runtime import runtime  # noqa: F401

BASE = "/api/data/v1/action-runs/OPS-R00/effects"
HEADERS = {"X-Demo-As": "U010001", "X-Ontology-Id": "1"}


@pytest.fixture
async def effect(runtime, monkeypatch):  # noqa: F811
    session, action, _ = runtime
    system = ExtSystem(
        code="FIX-EFFECT-SYS",
        name="限定测试系统",
        category="Webhook",
        vendor="本地",
        endpoint="https://127.0.0.1:9444",
        auth_type="无",
        auth_detail="",
        idempotent=True,
        idem_header="Idempotency-Key",
        timeout_ms=1000,
        max_retry=2,
        retry_text="",
        rate_limit="",
        owner="test",
        status=ExtSystemStatus.ok,
        env="测试",
        usage="只核重新排队不发送",
    )
    session.add(system)
    await session.refresh(action, attribute_names=["effects"])
    action.effects = [
        SideEffect(
            seq=0,
            kind=EffectKind.webhook,
            target="FIX-EFFECT-SYS:hook",
            when_text="执行成功后",
            note=json.dumps({"payload": {"value": {"param": "enabled"}}}),
        )
    ]
    run = await session.scalar(select(ActionRun).where(ActionRun.code == "OPS-R00"))
    run.status = ActionStatus.succeeded
    await session.flush()
    settings = SimpleNamespace(
        integration_real_connect=True,
        integration_adapters=SecretStr(
            json.dumps({"1/FIX-EFFECT-SYS": {"actions": ["ops_local"], "effect_paths": {"hook": "/hook"}}})
        ),
    )
    monkeypatch.setattr(adapters, "get_settings", lambda: settings)
    from app.modules.integration.action_edits import execution_definition

    definition = await execution_definition(session, run)
    declaration = definition["writeback"]["effects"][0]
    data = await action_effects.prepare_effect(
        session, await resolve_user(session, "U010001"), run, definition, declaration
    )
    row = await emit(
        session,
        action_effects.TOPIC,
        {
            "ontologyId": 1,
            "runId": run.code,
            "actor": "U010001",
            "typeVersion": "v1",
            "effect": declaration,
            "data": data,
            "state": "failed",
            "attempts": [
                {
                    "attempt": 1,
                    "httpStatus": 503,
                    "error": "EXTERNAL_HTTP_503",
                    "retryable": True,
                    "at": clock.now().isoformat(),
                    "signed": False,
                    "elapsedMs": 5,
                }
            ],
        },
        event_id="fix:effect-management",
    )
    row.attempts, row.last_error = 1, "EXTERNAL_HTTP_503"
    await session.flush()
    return session, row


async def test_current_authority_safe_requeue_preserves_history_and_idempotency(effect, client):
    session, row = effect
    result = await client.get(BASE, headers=HEADERS)
    assert result.status_code == 200 and result.json()["total"] == 1, result.text
    item = result.json()["items"][0]
    assert item["canRetry"] and item["history"][0]["httpStatus"] == 503
    assert "127.0.0.1" not in result.text and '"payload"' not in result.text
    queued = await client.post(
        f"{BASE}/{row.id}:retry", headers=HEADERS, json={"attempts": 1, "state": "failed"}
    )
    assert queued.status_code == 200 and queued.json()["state"] == "retrying", queued.text
    assert not queued.json()["canRetry"] and len(queued.json()["history"]) == 1
    assert row.event_id == "fix:effect-management" and row.attempts == 1
    assert row.payload["manualRetries"][0]["actor"] == "U010001"
    assert await session.scalar(select(AuditLog.id).where(AuditLog.action == "重试动作副作用"))
    # The same operator request cannot enqueue it twice after its state changed.
    assert (
        await client.post(f"{BASE}/{row.id}:retry", headers=HEADERS, json={"attempts": 1, "state": "failed"})
    ).status_code == 409


@pytest.mark.parametrize(
    "state,error",
    [
        ("unknown", "EXTERNAL_OUTCOME_UNKNOWN"),
        ("unknown", action_effects.CLAIMED),
        ("succeeded", None),
        ("failed", "EXTERNAL_HTTP_400"),
    ],
)
async def test_unknown_claimed_success_and_nonretryable_failure_never_requeue(effect, client, state, error):
    session, row = effect
    history = [{**row.payload["attempts"][0], "httpStatus": 400, "retryable": False}]
    row.payload = {**row.payload, "state": state, "attempts": history}
    row.last_error = error
    await session.flush()
    view = await client.get(BASE, headers=HEADERS)
    assert view.status_code == 200 and not view.json()["items"][0]["canRetry"]
    response = await client.post(
        f"{BASE}/{row.id}:retry", headers=HEADERS, json={"attempts": 1, "state": "failed"}
    )
    assert response.status_code == 409
    assert row.payload["state"] == state and "manualRetries" not in row.payload


async def test_workspace_and_current_permission_rechecked(effect, client):
    session, row = effect
    foreign = await emit(
        session, action_effects.TOPIC, {**row.payload, "ontologyId": 2}, event_id="fix:foreign-effect"
    )
    response = await client.get(BASE, headers=HEADERS)
    assert response.status_code == 200 and response.json()["total"] == 1
    assert (
        await client.post(
            f"{BASE}/{foreign.id}:retry", headers=HEADERS, json={"attempts": 0, "state": "failed"}
        )
    ).status_code == 404
    role = await session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    role.objs = []
    await session.flush()
    denied = await client.get(BASE, headers=HEADERS)
    assert denied.status_code in (403, 404)
