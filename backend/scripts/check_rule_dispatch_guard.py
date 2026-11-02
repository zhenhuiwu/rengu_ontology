"""Offline rule dispatch provenance/expiry and bounded read checks. No DB or HTTP."""
import ast
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table
from sqlalchemy.dialects import postgresql
import app.db.models
from app.core.errors import AppError
from app.modules.dynamic import rule_dispatch_guard as guard, rule_runtime, rule_relations, metric_rules
from app.modules.dynamic.models import Alert, SourceType
from app.modules.dynamic.rules_models import HitResult, Rule, RuleHit
from app.modules.integration import action_edits
from app.modules.static import metric_trial


async def main():
    at = datetime(2026, 11, 2, tzinfo=UTC)
    rule = NS(id=2, code="R-OFFLINE", version=4, status="published", expr="Device.score > 0",
              trigger={"kind": "定时", "enabled": True}, auto_stage={"enabled": True, "actionTypeId": "A", "validitySeconds": 0})
    run = NS(id=7, obj_kind="Device", obj_id="one", rule_code=rule.code, rule_version="4", source_type=SourceType.rule,
             submitted_at=at, alert_id=3, action_type_code="A", review_snapshot={})
    alert = NS(id=3, action_run_id=7, hit_code="H", rule_code=rule.code, obj_kind="Device", obj_id="one")
    hit = NS(code="H", alert_id=3, rule_id=2, rule_version=4, result=HitResult.alert, obj_kind="Device", obj_id="one")
    source = {"kind": "schedule", "scheduleRunId": 9}
    origin = guard.freeze_origin(run, rule, alert, source)
    assert origin["expiresAt"] is None
    rule.auto_stage["validitySeconds"] = 60
    assert guard.freeze_origin(run, rule, alert, source)["expiresAt"] == (at + timedelta(seconds=60)).isoformat()
    run.review_snapshot = {"origin": origin}
    async def scalar(stmt):
        return {Rule: rule, Alert: alert, RuleHit: hit}[stmt.column_descriptions[0]["expr"]]
    session = NS(scalar=AsyncMock(side_effect=scalar), get=AsyncMock(return_value=NS(rule_id=2, rule_version=4, definition={"expr": rule.expr})))
    evaluate = AsyncMock(return_value=True)
    async def rejected(code):
        try:
            await guard.validate_rule_dispatch(session, NS(), run)
        except AppError as exc:
            assert exc.code == code, (exc.code, code)
        else:
            raise AssertionError(code)
    with patch.object(guard.clock, "now", return_value=at), patch.object(guard, "check_func"), patch("app.modules.dynamic.sections.rule_def", return_value={"objKind": "Device", "expr": rule.expr}), patch.object(rule_runtime, "evaluate_target", evaluate):
        assert await guard.validate_rule_dispatch(session, NS(), run) is None
        assert evaluate.await_args.args[3] == "one"
        evaluate.return_value = False
        await rejected("RULE_ACTION_PREDICATE_CHANGED")
        evaluate.return_value = True
        rule.version = 5
        await rejected("RULE_ACTION_SOURCE_CHANGED")
        rule.version = 4
        origin["target"]["id"] = "another"
        await rejected("RULE_ACTION_ORIGIN_INVALID")
        origin["target"]["id"] = "one"
        run.review_snapshot = {}
        await rejected("RULE_ACTION_ORIGIN_MISSING")
        run.source_type = SourceType.qa
        assert await guard.validate_rule_dispatch(session, NS(), run) is None
        run.source_type = SourceType.rule
        run.review_snapshot = {"origin": {**origin, "expiresAt": at.isoformat()}}
        before = session.scalar.await_count
        await rejected("RULE_ACTION_EXPIRED")
        assert session.scalar.await_count == before  # Reject at boundary before any source/target query.

    table = Table("office_offline", MetaData(), Column("id", String), Column("scope", Integer), Column("score", Integer), Column("at", DateTime(timezone=True)))
    obj, gov = NS(code="Device"), NS(object_type="Device", inherit=None)
    row = {"id": "one", "score": 90}
    one = AsyncMock(return_value=row)
    binder = AsyncMock(return_value=NS(run=lambda row, at: (row["__identity"] == "one", "ok")))
    with patch.object(rule_runtime, "ensure_governed_tables", create=True), patch("app.common.sql_guard.ensure_governed_tables", AsyncMock()), patch.object(rule_runtime, "governed_tables", return_value={"x": gov}), patch.object(rule_runtime, "require_ontology_id", return_value=16), patch.object(action_edits, "existing_row", one), patch.object(metric_rules, "bind_metrics", binder):
        target_session = NS(scalar=AsyncMock(return_value=obj))
        assert await rule_runtime.evaluate_target(target_session, NS(), {"objKind": "Device", "expr": "Device.score > 0"}, "one", at=at)
        assert one.await_args.kwargs == {"lock": False}
        assert binder.await_args.kwargs["object_identity"] == "one" and binder.await_args.kwargs["source_row"]["id"] == "one"
        one.side_effect = AppError("ambiguous", code="AMBIGUOUS", status_code=409)
        try:
            await rule_runtime.evaluate_target(target_session, NS(), {"objKind": "Device", "expr": "True"}, "one", at=at)
        except AppError as exc:
            assert exc.code == "AMBIGUOUS"  # Never substitute a different/all-row lookup.
        else:
            raise AssertionError("nonunique identity accepted")

    statements = []
    async def execute(stmt):
        statements.append(stmt)
        return NS(mappings=lambda: NS(all=lambda: []), all=lambda: [("one", 90)])
    reader = NS(execute=execute)
    @asynccontextmanager
    async def read(_):
        yield reader
    binding = NS(id_col=table.c.id, cols={c.name: c for c in table.c}, read=read, source=None)
    bounded = NS(object=NS(code="Device"), governed=NS(table=table, binding=binding, source_metadata=None))
    async def row_filter(stmt, *args):
        return stmt.where(table.c.scope == 16)
    metric_session = NS(scalars=AsyncMock(return_value=[]))
    with patch.object(metric_trial, "check_func"), patch.object(metric_trial, "metric_definition", AsyncMock(return_value={"formula": "sum", "version": 1})), patch("app.common.sql_guard.ensure_governed_tables", AsyncMock()), patch.object(metric_trial, "require_ontology_id", return_value=16), patch.object(metric_trial, "execution_bindings", AsyncMock()), patch.object(metric_trial, "compile_metric", return_value=(bounded, table.c.score, table.c.at, "at", [])), patch("app.common.row_filter.apply_row_filter", row_filter):
        await metric_trial.evaluate_metric(metric_session, NS(), "M", at=at, timezone="UTC", object_kind="Device", object_identity="one")
        sql = str(statements[-1].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "CAST(office_offline.id AS VARCHAR) = 'one'" in sql and "office_offline.scope = 16" in sql
        await metric_trial.evaluate_metric(metric_session, NS(), "M", at=at, timezone="UTC", object_kind="Device")
        assert "= 'one'" not in str(statements[-1])  # Existing public default unchanged.

    dst = NS(code="Related", properties=[NS(api_name="score", backing_column="score", source_column=None, is_sensitive=False, visibility="visible", transform=None)])
    relation_gov = NS(table=table, projection=None, columns={c.name: c for c in table.c}, links={})
    gov.table = table
    relation_session = NS(execute=execute)
    with patch.object(rule_relations, "check_object_read"), patch.object(rule_relations, "read_relation_binding", AsyncMock(return_value=(NS(api_name="relation"), obj, gov, dst, relation_gov, table.c.id, table.c.id))), patch.object(rule_relations, "apply_row_filter", row_filter):
        result = await rule_relations.bind_rule_relations(relation_session, NS(), ast.parse('related("relation", "score") == 90', mode="eval"), obj, gov, source_row={"id": "one"})
        sql = str(statements[-1].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "office_offline.id = 'one'" in sql and "scope = 16" in sql and "LIMIT 2" in sql and result
    print("rule dispatch guard: expiry/source/version/predicate/unique target/metric identity/relation scope passed (offline)")


if __name__ == "__main__":
    asyncio.run(main())
