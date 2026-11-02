"""Offline action outcomes, canonical alert links, visibility and version-window checks."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, select
from sqlalchemy.dialects import postgresql
import app.db.models
from app.contracts.actions import ReconcileOutcome, ReconcileResult, SubmitResult, SubmitStatus
from app.core.errors import IdempotencyConflict
from app.modules.dynamic import service
from app.modules.dynamic.models import ActionStatus, Alert, AlertStatus, ActionRun, ReconcileResult as StoredResult, SourceType
from app.modules.dynamic.rules_models import HitResult, RuleHit
from app.modules.dynamic.schemas import SourceIn, StageIn
from app.modules.event import exceptions


def fixture():
    alert = NS(id=11, code="AL-OFFLINE", ontology_id=16, obj_kind="Device", obj_id="one", rule_code="R-OFFLINE", action_run_id=None, status=AlertStatus.processing)
    run = NS(id=7, code="ACT-OFFLINE", ontology_id=16, alert_id=11, obj_kind="Device", obj_id="one", rule_code=alert.rule_code,
             retry_of_id=None, status=ActionStatus.unknown, reconcile_result=None, ext_ref=None, unknown_reason="等待外部结果", fail_reason=None,
             sys_code="EXT-OFFLINE", action_type_code="office_action", idem_key="offline-key", result_code=None)
    order = []
    async def scalar(statement):
        expr = statement.column_descriptions[0]["expr"]
        if expr is Alert:
            order.append("alert")
            if "action_run_id" in str(statement.whereclause) and alert.action_run_id != run.id:
                return None
            return alert
        if expr is ActionRun:
            return run
        return session.other_run_id
    session = NS(scalar=AsyncMock(side_effect=scalar), flush=AsyncMock(), other_run_id=None)
    repo = NS(add_log=AsyncMock(return_value=NS(seq=1)))
    ports = NS(emitter=NS(emit=AsyncMock()), scheduler=NS(resolve_reconcile=AsyncMock(), reopen_reconcile=AsyncMock()))
    owner = NS(session=session, repo=repo, catalog=NS(system_name=lambda _: "Office receiver", action_meta=lambda _: {}, timeout_sec=lambda _: 5), ports=ports,
               user=NS(id="admin", name="admin", ontology_id=16, is_service_account=False), get=AsyncMock(), _operation_access=AsyncMock(), _log=AsyncMock(return_value=1), _emit=AsyncMock())
    return alert, run, session, owner, order


async def main():
    alert_log = AsyncMock()
    with patch.object(service.AlertRepo, "add_log", alert_log):
        alert, run, session, owner, order = fixture()
        await service.bind_alert_run(session, run, alert)
        assert alert.action_run_id == run.id
        assert "FOR NO KEY UPDATE" in str(session.scalar.call_args_list[0].args[0].compile(dialect=postgresql.dialect()))
        await service.bind_alert_run(session, run, alert)
        for change in ({"obj_id": "other"}, {"ontology_id": 17}, {"rule_code": "other"}):
            previous = {key: getattr(run, key) for key in change}
            for key, value in change.items(): setattr(run, key, value)
            try:
                await service.bind_alert_run(session, run, alert)
            except IdempotencyConflict:
                pass
            else:
                raise AssertionError("mismatched alert context must not bind")
            for key, value in previous.items(): setattr(run, key, value)
        alert.action_run_id = 99
        try:
            await service.bind_alert_run(session, run, alert)
        except IdempotencyConflict:
            pass
        else:
            raise AssertionError("another canonical run must not be overwritten")
        parent = NS(id=99, status=ActionStatus.failed, alert_id=alert.id, ontology_id=16)
        run.retry_of_id = 99
        await service.bind_alert_run(session, run, alert, retry_of=parent)
        assert alert.action_run_id == run.id
        alert.action_run_id, session.other_run_id = None, 88
        try:
            await service.bind_alert_run(session, run, alert, retry_of=parent)
        except IdempotencyConflict:
            pass
        else:
            raise AssertionError("an ambiguous missing canonical pointer cannot be claimed over another run")
        session.other_run_id = None

        # Exercise the actual common creator, not merely the link helper.
        alert.action_run_id = None
        async def create(row, **_kwargs):
            row.id, row.ontology_id = run.id, 16
            return row
        creator = NS(session=session, user=owner.user, catalog=owner.catalog,
                     repo=NS(next_code=AsyncMock(return_value=run.code), create=create))
        created = await service.ActionService.create_run(creator, type_={"id": "type", "name": "Office"}, status=ActionStatus.staged,
            source_type=SourceType.qa, idem_key="offline", initiator="admin", confirmer=None, approver=None,
            obj_kind="Device", obj_id="one", obj_label="one", params=None, alert=alert, rule_code=alert.rule_code)
        assert created.alert_id == alert.id and alert.action_run_id == created.id

        # Generic stage replay locks the original run before the alert and repairs a missing back-link.
        alert.action_run_id = None
        owner._type_of = lambda **_: {"id": "type", "name": "Office"}
        owner.alerts = NS(get_by_code=AsyncMock(return_value=alert))
        owner._replay_or_conflict = AsyncMock(return_value=run)
        async def locked(*_args):
            order.append("run")
            return run
        order.clear()
        with patch("app.db.dialects.get_adapter", return_value=NS(xact_lock=AsyncMock())), \
             patch("app.modules.action.policy.load", AsyncMock()), patch("app.modules.action.policy.gate"), \
             patch("app.modules.integration.reconcile.lock_run", side_effect=locked):
            result, fresh = await service.ActionService.stage(owner, StageIn(type_id="type", obj_kind="Device", obj_id="one", obj_label="one",
                    source=SourceIn(type="问数", alert_id=alert.code)))
        assert result is run and not fresh and alert.action_run_id == run.id and order[:2] == ["run", "alert"]

        failure = ReconcileResult(ReconcileOutcome.not_executed, "OFFICE-REJECT", {"source": "submit", "reason": "合成拒绝原因", "code": "DECLINED"})
        await service.DynamicReconciliationListener.on_reconciled(owner, run.code, failure)
        assert run.status == ActionStatus.reconciled and run.reconcile_result == StoredResult.not_executed
        assert run.fail_reason == "合成拒绝原因" and run.result_code == "DECLINED" and run.ext_ref == "OFFICE-REJECT" and run.unknown_reason is None
        assert alert.status == AlertStatus.processing and owner.ports.emitter.emit.await_count == 0
        assert alert_log.call_args.kwargs["abnormal"] is True and "明确返回" in alert_log.call_args.kwargs["text"]
        count = alert_log.await_count
        await service.DynamicReconciliationListener.on_reconciled(owner, run.code, failure)
        assert alert_log.await_count == count, "same terminal callback must not duplicate alert facts"

        alert, run, session, owner, _ = fixture()
        alert.action_run_id = run.id
        await service.DynamicReconciliationListener.on_reconciled(owner, run.code, ReconcileResult(ReconcileOutcome.succeeded, "OFFICE-OK"))
        assert run.unknown_reason is None and run.fail_reason is None and run.ext_ref == "OFFICE-OK"
        assert owner.ports.emitter.emit.await_count == 1
        run.status, run.unknown_reason = ActionStatus.unknown, "仍未确认"
        await service.ActionService.reconcile(owner, run.code, "仍无法确认", "合成查询尚无结论")
        assert run.unknown_reason == "仍未确认", "nonterminal uncertainty must remain active"
        await service.ActionService.reconcile(owner, run.code, "外部未执行", "已独立核实合成未执行")
        assert run.unknown_reason is None and run.fail_reason == "已独立核实合成未执行"
        for response in (SubmitResult(SubmitStatus.FAILED, receipt_id="REJECT-1", reason="明确拒绝"), SubmitResult(SubmitStatus.UNKNOWN, reason="等待执行器", code="QUEUED")):
            alert, run, session, owner, _ = fixture()
            alert.action_run_id = run.id
            run.status = ActionStatus.executing
            owner.ports.submitter = NS(submit=AsyncMock(return_value=response))
            await service.ActionService._submit(owner, run)
            if response.status == SubmitStatus.FAILED:
                assert run.unknown_reason is None and run.ext_ref == "REJECT-1" and run.fail_reason == "明确拒绝"
            else:
                assert run.status == ActionStatus.unknown and run.unknown_reason == "等待执行器"

    # Full grants can see ambiguous identity metadata; partial grants retain unique-only projection.
    rows = Table("offline_device", MetaData(), Column("id", Integer), Column("ontology_id", Integer), Column("visible", Boolean))
    refs = Table("offline_refs", MetaData(), Column("row_id", Integer), Column("identity", String))
    mapping = NS(inherit=None, object_type="Device", source_sql=None, scope_column="ontology_id", links={})
    flags = []
    def identities(_mapping, *, unique_only):
        flags.append(unique_only)
        return select(refs.c.row_id, refs.c.identity)
    for scope in (None, {"op": "visible"}):
        with patch.object(exceptions, "ensure_governed_tables", AsyncMock(), create=True), \
             patch("app.common.sql_guard.ensure_governed_tables", AsyncMock()), \
             patch.object(exceptions, "governed_tables", return_value={"device": mapping}), \
             patch.object(exceptions, "row_filter", AsyncMock(return_value=scope)), patch.object(exceptions, "is_deny_all", return_value=False), \
             patch.object(exceptions, "row_source", return_value=rows), patch.object(exceptions, "row_identity_column", return_value=rows.c.id), \
             patch.object(exceptions, "source_columns", return_value={}), patch.object(exceptions, "to_sql", return_value=rows.c.visible.is_(True)), \
             patch.object(exceptions, "identity_rows", side_effect=identities):
            await exceptions.visible_events(NS(), NS(ontology_id=16, id="admin", attrs={}))
    assert flags == [False, True]
    for name in ("event_rule_runtime.py", "scheduler.py"):
        path = Path(__file__).resolve().parents[1] / "app/modules/dynamic" / name
        tree = ast.parse(path.read_text("utf-8"))
        value = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "recent" for t in node.targets))
        query = eval(compile(ast.Expression(value), str(path), "eval"), {"select": select, "RuleHit": RuleHit, "rule": NS(id=5, version=4), "key": "cycle", "HitResult": HitResult})
        compiled = query.compile(dialect=postgresql.dialect())
        assert "rule_hit.rule_version" in str(compiled) and compiled.params["rule_version_1"] == 4
    print("Action outcome/link branches, no duplicate callback, active reason, scoped inbox and version windows passed offline.")


if __name__ == "__main__":
    asyncio.run(main())
