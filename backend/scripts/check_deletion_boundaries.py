"""Offline definition/record deletion boundaries; no database, HTTP, queue or migrations."""

import asyncio
from types import SimpleNamespace as Namespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import app.db.models  # noqa: F401
from app.api import include_routers
from app.common.context import ViewContext
from app.core.errors import AppError, IdempotencyConflict, NotFound, VersionConflict
from app.modules.action.service import defs_for_view
from app.modules.dynamic import record_delete as records
from app.modules.dynamic import row_scope
from app.modules.dynamic.models import ActionRun, ActionStatus, AlertStatus
from app.modules.dynamic.rules_service import RuleService
from app.modules.dynamic.service import ActionService, bind_alert_run
from app.modules.functions.service import current_select
from app.modules.static import definition_delete as definitions
from app.modules.static.router import router as static_router


async def main():
    user = Namespace(
        id="offline",
        name="offline",
        ontology_id=16,
        ontology_role="owner",
        is_service_account=False,
        ontology_ids=None,
        func={spec.page: "manage" for spec in definitions.SPECS.values()},
    )
    user.func.update({"releases": "manage", "action-center": "manage", "alerts": "manage"})
    adapter = Namespace(xact_lock=AsyncMock())
    latest = Namespace(id=4, ver="old")
    for resource in (
        "link-types",
        "functions",
        "action-types",
        "event-types",
        "rules",
        "ext-systems",
        "metrics",
        "knowledge",
    ):
        spec = definitions.SPECS[resource]
        row = Namespace(
            id=9,
            code="OFFLINE",
            lock_version=3,
            status="published",
            version=1,
            retired_release_id=None,
            trigger={"enabled": True},
            auto_stage={"enabled": True},
            action_type_id=8,
        )
        snapshot = {spec.section: {row.code: {"id": row.code}, "other": {"id": "other"}}}
        versioning = Namespace(
            provider=Namespace(build_snapshot=AsyncMock(return_value=snapshot)),
            _append=AsyncMock(return_value=Namespace(id=5, ver="new")),
        )
        session = Namespace(info={"ontology_id": 16}, flush=AsyncMock())
        hard = AsyncMock()
        with (
            patch.object(definitions, "get_adapter", return_value=adapter),
            patch.object(
                definitions, "ReleaseRepo", return_value=Namespace(latest=AsyncMock(return_value=latest))
            ),
            patch.object(definitions, "load", AsyncMock(return_value=row)),
            patch.object(definitions, "references", AsyncMock(return_value=[])) as refs,
            patch.object(definitions, "VersioningService", return_value=versioning),
            patch.object(definitions, "_hard_delete", hard),
            patch.object(definitions, "insert_audit", AsyncMock()),
        ):
            refs.return_value = ["规则 R-OFFLINE 引用该定义"]
            try:
                await definitions.remove(
                    session, user, spec, row.code, definitions.DeleteIn(lock_version=4, target_version=3)
                )
            except AppError as error:
                assert error.code == "DEFINITION_DELETE_BLOCKED" and "R-OFFLINE" in error.message
            else:
                raise AssertionError("referenced definition deleted")
            hard.assert_not_awaited()
            refs.return_value = []
            result = await definitions.remove(
                session, user, spec, row.code, definitions.DeleteIn(lock_version=4, target_version=3)
            )
            assert result == {"ver": "new"}
            if spec.section:
                assert "OFFLINE" not in snapshot[spec.section] and "other" in snapshot[spec.section]
            if spec.mode == "hard":
                hard.assert_awaited_once_with(session, spec.model, [row])
            else:
                hard.assert_not_awaited()
                assert not definitions.active(row, spec)
            count = versioning._append.await_count
            try:
                await definitions.remove(
                    session, user, spec, row.code, definitions.DeleteIn(lock_version=3, target_version=3)
                )
            except VersionConflict:
                pass
            else:
                raise AssertionError("stale release accepted")
            assert versioning._append.await_count == count
    assert definitions.mentions({"actionTypes": {"A-01": True}}, {"A-01"})
    assert not definitions.mentions("A-010", {"A-01"})
    spec = definitions.SPECS["functions"]
    assert definitions.active(Namespace(status="disabled"), spec)
    sql = str(current_select().compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "function.status != 'deleted'" in sql
    visible = await defs_for_view(
        None,
        ViewContext(),
        [Namespace(code="gone", retired_release_id=5), Namespace(code="disabled", retired_release_id=None)],
        "actionTypes",
        lambda row: {"id": row.code},
    )
    assert visible == [{"id": "disabled"}]
    rule_service = RuleService(
        Namespace(scalar=AsyncMock(side_effect=[Namespace(id=2, version=None), None])), user
    )
    try:
        await rule_service.get("gone")
    except NotFound:
        pass
    else:
        raise AssertionError("deleted rule by-id returned")
    # Immutable function versions are owned history, while unknown/real external FKs block.
    edges = [("public", "fn_version", "function_id", "function", "id", 1)]
    session = Namespace(
        execute=AsyncMock(return_value=Namespace(all=lambda: edges)),
        scalar=AsyncMock(return_value=1),
        info={"ontology_id": 16},
    )
    assert await definitions.fk_references(session, Namespace(id=9), spec) == []
    session.scalar.assert_not_awaited()
    edges[:] = [("other_schema", "unknown", "function_id", "function", "id", 1)]
    assert "未确认" in (await definitions.fk_references(session, Namespace(id=9), spec))[0]

    run = Namespace(
        id=7,
        code="ACT-OFFLINE",
        lock_version=3,
        status=ActionStatus.unknown,
        deleted_at=None,
        deleted_by=None,
        idem_key="retain-idempotency",
        is_seed=False,
        alert_id=11,
        params={"score": 90},
    )
    session = Namespace(scalar=AsyncMock(return_value=None))
    assert await records.action_reason(session, run)
    session.scalar.assert_not_awaited()
    run.status = ActionStatus.succeeded
    assert await records.action_reason(session, run) is None
    for blocked, text in (([1], "外部调用"), ([None, 1], "对账"), ([None, None, 1], "副作用")):
        session.scalar = AsyncMock(side_effect=blocked)
        assert text in await records.action_reason(session, run)
    alert = Namespace(
        id=11,
        code="AL-OFFLINE",
        lock_version=2,
        status=AlertStatus.open,
        deleted_at=None,
        action_run_id=run.id,
    )
    assert await records.alert_reason(session, alert)
    alert.status = AlertStatus.resolved
    run.status = ActionStatus.unknown
    session.scalars = AsyncMock(return_value=[run])
    assert await records.alert_reason(session, alert)
    run.status = ActionStatus.succeeded

    async def scalar(statement):
        return run if statement.column_descriptions[0]["expr"] is ActionRun else None

    session = Namespace(scalar=AsyncMock(side_effect=scalar), flush=AsyncMock())
    svc = Namespace(session=session, user=user, get=AsyncMock(return_value=run), _log=AsyncMock())
    with patch.object(records, "insert_audit", AsyncMock()):
        await records.remove_action(svc, run.code, 3)
    assert run.deleted_at is not None and run.deleted_by == user.id and run.idem_key == "retain-idempotency"
    assert run.alert_id == 11 and run.params == {"score": 90} and run.status == ActionStatus.succeeded
    svc._log.assert_awaited_once()
    archived_alert = Namespace(id=11, deleted_at=run.deleted_at)
    try:
        await bind_alert_run(Namespace(scalar=AsyncMock(return_value=archived_alert)), run, archived_alert)
    except NotFound:
        pass
    else:
        raise AssertionError("deleted alert rebound")
    replay = Namespace(repo=Namespace(by_idem_key=AsyncMock(return_value=[run])))
    try:
        await ActionService._replay_or_conflict(replay, run.idem_key, {})
    except IdempotencyConflict:
        pass
    else:
        raise AssertionError("deleted idempotency record replayed")
    with (
        patch.object(row_scope, "ensure_governed_tables", AsyncMock()),
        patch.object(row_scope, "governed_tables", return_value={}),
    ):
        statement = await row_scope.scoped_instances(
            None, select(ActionRun), user, ActionRun.obj_kind, ActionRun.obj_id
        )
    assert "action_run.deleted_at IS NULL" in str(statement._where_criteria[0])
    # Retention is row state only: no worker, hard DELETE or log deletion is invoked.
    app = FastAPI()
    include_routers(app)
    included = [getattr(route, "original_router", None) for route in app.routes]
    assert included.index(definitions.router) < included.index(static_router)
    assert any(route.path == "/link-types/{id}:delete-check" for route in definitions.router.routes)
    print("deletion: version/refs/current reads/terminal/audit/idempotency/routes passed (offline)")


if __name__ == "__main__":
    asyncio.run(main())
