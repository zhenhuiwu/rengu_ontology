"""In-memory QA action contract checks; no database, HTTP, workers or real submitter."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import Response
from sqlalchemy import Column, MetaData, String, Table
from sqlalchemy.dialects import postgresql

from app.core.errors import AppError, IdempotencyConflict, NotFound
from app.core.state import InvalidTransition
from app.modules.action.checker import DatabaseSubmissionChecker
from app.modules.dynamic.models import ActionStatus, AlertStatus
from app.modules.dynamic.service import ActionService
from app.modules.qa import chain, intent, router
from app.modules.qa.models import TurnKind
from app.modules.qa.schemas import TurnActionStageIn


def card():
    return {
        "stageVersion": 1,
        "stage": {"typeId": "example", "objKind": "example-object", "objId": "one"},
        "params": {"note": "new"},
    }


def run(**values):
    return SimpleNamespace(**{
        "code": "EXAMPLE-RUN", "qa_session": "EXAMPLE-SESSION", "action_type_code": "example",
        "obj_kind": "example-object", "obj_id": "one", "params_text": '{"note": "new"}',
        "alert_id": None, **values,
    })


async def stage_check(action_card, *, rows=(), conflict=None, alert=None, alert_error=None, staged=None):
    turn = SimpleNamespace(
        kind=TurnKind.answer, session_id=1, snapshot={"answer": {"card": "action", "action": action_card}},
    )
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(code="EXAMPLE-SESSION", title="Example")),
        scalar=AsyncMock(side_effect=list(rows)),
    )
    user = SimpleNamespace(id="example-user", func={"alerts": "manage"}, ontology_role="owner")
    actions = SimpleNamespace(
        stage=AsyncMock(return_value=staged or (run(alert_id=alert.id if alert else None), True)),
        detail=AsyncMock(side_effect=lambda r: r),
        session=session, user=user, ports=None,
        catalog=SimpleNamespace(person=lambda value: {"id": value}),
    )
    if conflict:
        actions.stage.side_effect = conflict
    response = Response(status_code=201)
    with (
        patch.object(router.svc, "effective_user", AsyncMock(return_value=(1, None))),
        patch.object(router, "_own_turn", AsyncMock(return_value=turn)),
        patch.object(router, "get_adapter", return_value=SimpleNamespace(xact_lock=AsyncMock())),
        patch.object(router, "audit", AsyncMock()),
        patch.object(router.AlertService, "get", AsyncMock(return_value=alert, side_effect=alert_error)),
        patch.object(intent, "action_policy", AsyncMock(return_value={"mode": "confirm"})),
    ):
        result = await router.stage_turn_action(
            "EXAMPLE-TURN", session, user,
            SimpleNamespace(preview_as=None), actions, response, TurnActionStageIn(),
        )
    return result, turn, actions, session, response


class MemoryActions(ActionService):
    def __init__(self, *, free=True, blocked=False):
        self.session = object()
        self.user = SimpleNamespace(id="example-user", name="Example")
        self.catalog = SimpleNamespace(
            action_meta=lambda _: {"approvalConfigured": True, "approvalRequired": not free},
            person=lambda _: None,
        )
        self.record = run(status=ActionStatus.staged, confirmer=None, approver=None)
        self.submits = 0
        self.blocked = blocked

    async def get(self, _):
        return self.record

    async def _operation_access(self, _):
        pass

    def _not_mine(self, _):
        return False

    async def _check_criteria(self, *_):
        if self.blocked:
            raise AppError("Example precheck rejected", code="SUBMISSION_CRITERIA_FAILED", status_code=422)

    async def _log(self, *_):
        pass

    async def _submit(self, record):
        self.submits += 1
        record.status = ActionStatus.succeeded


async def main():
    # A hierarchy parent identifies its object/table but need not have a column.
    # ColumnCollection membership rejects None/integers before QA can make an action card.
    @asynccontextmanager
    async def nested():
        yield

    dimension = {
        "kind": "dimension", "binding": {"object": "example-object", "table": "source_example"},
        "levels": [{"table": "source_example", "column": c} for c in ("status", None, 5, "missing")],
    }
    table = Table("local_example", MetaData(), Column("status", String))
    memory = SimpleNamespace(info={"ontology_id": 17}, begin_nested=nested)
    for source in (table, table.alias("mirror_example")):
        bound = SimpleNamespace(name=source.name, object_type="example-object", inherit=False, table=source)
        with (
            patch("app.common.sql_guard.ensure_governed_tables", AsyncMock()),
            patch("app.contracts.static.semantic_map", AsyncMock(return_value=[dimension])),
            patch.object(chain, "qa_source", return_value=SimpleNamespace(tables=[])),
            patch.object(chain, "governed_tables", return_value={bound.name: bound}),
            patch.object(chain.vi, "build", side_effect=lambda _cols, rows, _extra: rows),
        ):
            dimensions = await chain.qa_value_index(memory)
        assert dimensions[0]["binding"]["table"] == "source_example"
        assert dimensions[0]["levels"][0]["table"] == source.name
        assert dimensions[0]["levels"][1:] == dimension["levels"][1:]

    # Preflight probes carry trusted transaction scope, never model/context-supplied scope.
    memory.scalars = AsyncMock(return_value=[])
    user = SimpleNamespace(id="example-user", is_service_account=False, ontology_id=999)
    checker = DatabaseSubmissionChecker(memory, user)
    action = {"params": [], "conds": [], "writeback": {"edits": [], "effects": []}}
    context = {"params": {}, "objKind": "example-object", "objId": "one", "ontology_id": 999}
    with (
        patch("app.modules.integration.action_edits.resolved_params", AsyncMock(return_value={})),
        patch("app.modules.admin.roles.RoleService.effective_access", AsyncMock(return_value=([], None))),
        patch.object(checker, "context_references", AsyncMock(return_value={})),
        patch("app.modules.integration.action_edits.external_webhook", AsyncMock(return_value=False)),
        patch("app.modules.integration.action_edits.business_edits", AsyncMock()) as edits,
        patch("app.modules.integration.action_effects.preflight_effects", AsyncMock()) as effects,
    ):
        assert (await checker.run(action, context))["passed"]
        probe = edits.await_args.args[2]
        assert probe.ontology_id == 17 and effects.await_args.args[2] is probe
        assert probe.code == "DRY-RUN" and probe.obj_id == "one"
        effects.reset_mock()
        edits.side_effect = AppError("Example mapping rejected", code="ACTION_EDITS_UNAVAILABLE")
        blocked = await checker.run(action, context)
        assert not blocked["passed"] and blocked["failed"][0]["fail"] == "Example mapping rejected"
        effects.assert_not_awaited()

    assert router._stage_params({"note": "new"}) == '{"note": "new"}'
    result, turn, actions, session, response = await stage_check(card(), rows=[None])
    assert result.code == "EXAMPLE-RUN" and response.status_code == 201
    assert turn.snapshot["answer"]["action"]["runId"] == result.code
    assert actions.stage.await_count == 1
    body = actions.stage.await_args.args[0]
    assert body.source.type == "问数" and body.source.confirmer == "example-user"
    assert body.params == '{"note": "new"}'
    compiled = session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "action_run.obj_kind =" in sql and "action_run.params_text =" in sql
    assert "action_run.alert_id IS NULL" in sql
    assert "example-object" in compiled.params.values() and body.params in compiled.params.values()

    # The action service may replay its own idempotency key without raising a conflict.
    # QA must validate that returned record before binding it to this card.
    for changed in (
        {"action_type_code": "another-action"}, {"obj_kind": "another-kind"},
        {"obj_id": "another-object"}, {"params_text": '{"note": "old"}'}, {"alert_id": 101},
    ):
        action_card = card()
        try:
            await stage_check(action_card, rows=[None], staged=(run(**changed), False))
        except AppError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("Mismatched service replay was bound to the QA card")

    previous = run()
    result, turn, actions, _, response = await stage_check(card(), rows=[None], staged=(previous, False))
    assert result is previous and response.status_code == 200
    assert turn.snapshot["answer"]["action"]["runId"] == previous.code
    assert actions.stage.await_count == 1

    previous = run()
    replay = {**card(), "runId": previous.code}
    result, _, actions, _, response = await stage_check(replay, rows=[previous])
    assert result is previous and response.status_code == 200 and actions.stage.await_count == 0
    legacy = card()
    legacy.pop("stageVersion")
    try:
        await stage_check(legacy)
    except AppError as exc:
        assert exc.code == "LEGACY_ACTION_CARD"
    else:
        raise AssertionError("Unbound historical card was staged")

    # A same-alert record with other parameters must not be silently executed.
    previous.params_text = '{"note": "old"}'
    try:
        await stage_check(card(), rows=[None, previous], conflict=IdempotencyConflict(details={"id": previous.code}))
    except AppError as exc:
        assert exc.code == "IDEMPOTENCY_CONFLICT"
    else:
        raise AssertionError("Different alert parameters were reused")

    alert_a = SimpleNamespace(
        id=101, code="EXAMPLE-ALERT-A", status=AlertStatus.processing,
        assignee="example-user", obj_kind="example-object", obj_id="one",
    )
    alert_b = SimpleNamespace(**{**vars(alert_a), "id": 102, "code": "EXAMPLE-ALERT-B"})
    for current in (alert_a, alert_b):
        current_card = {**card(), "decision": {"alertId": current.code}}
        _, _, actions, session, _ = await stage_check(current_card, rows=[None], alert=current)
        query = session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
        assert "action_run.alert_id =" in str(query) and current.id in query.params.values()
        assert actions.stage.await_args.args[0].source.alert_id == current.code

    # Same contents cannot reuse a record from another alert, or cross no-alert/alert context.
    for current, prior_id in ((alert_b, alert_a.id), (alert_a, None), (None, alert_a.id)):
        current_card = {**card(), **({"decision": {"alertId": current.code}} if current else {})}
        previous = run(alert_id=prior_id)
        try:
            await stage_check(
                current_card, rows=[None, previous], alert=current,
                conflict=IdempotencyConflict(details={"id": previous.code}),
            )
        except AppError as exc:
            assert exc.code == "IDEMPOTENCY_CONFLICT"
        else:
            raise AssertionError("Different alert context was reused")

    current_card = {**card(), "decision": {"alertId": alert_a.code}}
    # The service's alert-key replay compares obj_id but not obj_kind.
    try:
        await stage_check(
            current_card, rows=[None], alert=alert_a,
            staged=(run(obj_kind="another-kind", alert_id=alert_a.id), False),
        )
    except AppError as exc:
        assert exc.code == "IDEMPOTENCY_CONFLICT"
    else:
        raise AssertionError("Same-alert service replay changed the target kind")

    previous = run(qa_session=None, alert_id=alert_a.id)
    result, _, _, _, response = await stage_check(
        current_card, rows=[None], alert=alert_a, staged=(previous, False),
    )
    assert result is previous and result.qa_session is None and response.status_code == 200

    previous = run(alert_id=alert_a.id)
    result, _, _, _, response = await stage_check(current_card, rows=[previous], alert=alert_a)
    assert result is previous and response.status_code == 200
    replay = {**current_card, "runId": previous.code}
    _, _, _, session, _ = await stage_check(replay, rows=[previous], alert=alert_a)
    query = session.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    assert "action_run.alert_id =" in str(query) and alert_a.id in query.params.values()

    # No fake run is queried/remembered when the alert can no longer be operated on.
    for changed in (
        {"status": AlertStatus.resolved}, {"assignee": "someone-else"}, {"obj_id": "another-object"},
    ):
        try:
            await stage_check(current_card, alert=SimpleNamespace(**{**vars(alert_a), **changed}))
        except AppError:
            pass
        else:
            raise AssertionError("An inoperable/mismatched alert was reused")
    try:
        await stage_check(current_card, alert_error=NotFound("Alert is no longer visible"))
    except NotFound:
        pass
    else:
        raise AssertionError("An invisible alert was reused")

    service = MemoryActions()
    with patch("app.modules.integration.reconcile.lock_run", AsyncMock(return_value=service.record)):
        assert (await service.confirm(service.record.code)).status == ActionStatus.succeeded
        try:
            await service.confirm(service.record.code)
        except InvalidTransition:
            pass
        else:
            raise AssertionError("Confirmed twice")
    assert service.submits == 1

    service = MemoryActions(free=False)
    with patch("app.modules.integration.reconcile.lock_run", AsyncMock(return_value=service.record)):
        assert (await service.confirm(service.record.code)).status == ActionStatus.approving
    assert service.submits == 0

    service = MemoryActions(blocked=True)
    with patch("app.modules.integration.reconcile.lock_run", AsyncMock(return_value=service.record)):
        try:
            await service.confirm(service.record.code)
        except AppError as exc:
            assert exc.code == "SUBMISSION_CRITERIA_FAILED"
        else:
            raise AssertionError("Precheck was bypassed")
    assert service.submits == 0 and service.record.status == ActionStatus.staged
    print("QA inline action memory contract: passed (no database or external calls)")


if __name__ == "__main__":
    asyncio.run(main())
