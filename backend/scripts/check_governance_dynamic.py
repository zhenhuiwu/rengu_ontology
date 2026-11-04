"""Compile runtime-origin authorization and exercise policy fakes without a database or HTTP."""

import ast
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

import app.db.models  # noqa: F401 - register all mapped foreign keys without connecting
from app.core.errors import Forbidden
from app.modules.dynamic.models import ActionRun, Alert
from app.modules.dynamic.rules_models import RuleHit
from app.modules.event.models import Event
from app.modules.event.repo import EventRepo
from app.modules.governance import dynamic_guard
from app.modules.governance.policy import effective_policies
from app.modules.static.lineage import identity

ROOT = Path(__file__).resolve().parents[1]


def compile_checks():
    ctx = {
        "allowed": {"rule": {"R": "3"}, "action": {"A": "v1"}, "event": {"E": "v1"}},
        "watermark": datetime(2026, 11, 3, tzinfo=UTC),
        "eventTypes": {"E": 5},
        "releaseId": 9,
    }
    for model in (Alert, ActionRun, RuleHit, Event):
        base = select(model.id).where(dynamic_guard.predicate(model, ctx))
        page = str(base.limit(10).offset(20).compile(dialect=postgresql.dialect()))
        count = str(select(func.count()).select_from(base.subquery()).compile(dialect=postgresql.dialect()))
        assert "created_at >" in page and "created_at >" in count
        assert page.index("WHERE") < page.index("LIMIT")
        assert "WHERE" in count and "count(*)" in count
        assert str(dynamic_guard.predicate(model, None)) == "true"
        denied = {**ctx, "allowed": {"rule": {}, "action": {}, "event": {}}}
        assert "false" in str(dynamic_guard.predicate(model, denied)).lower()
        assert str(dynamic_guard.predicate(model, {**ctx, "watermark": None})) == "false"
    event_sql = str(
        select(Event.id).where(dynamic_guard.predicate(Event, ctx)).compile(dialect=postgresql.dialect())
    )
    assert "definition_snapshot" in event_sql and "release_id" in event_sql
    action_sql = str(
        select(ActionRun.id)
        .where(dynamic_guard.predicate(ActionRun, ctx))
        .compile(dialect=postgresql.dialect())
    )
    assert "alert.rule_code" in action_sql and "action_run.rule_version" in action_sql
    # Field markings cover opaque event summaries and consumers, without pretending to mask fragments of text.
    field, event, rule = identity("event_field", "E", "secret"), identity("event", "E"), identity("rule", "R")
    graph = {
        "nodes": [{"id": n} for n in (field, event, rule)],
        "edges": [
            {"source": field, "target": event},
            {"source": event, "target": field},
            {"source": event, "target": rule},
        ],
        "unresolved": [],
    }
    states = effective_policies(
        {"resources": [{"resource": field, "classes": ["private"], "purposes": ["p"], "mask": "null"}]}, graph
    )
    assert states[event]["mask"] == states[rule]["mask"] == "null"
    assert states[event]["classes"] == ["private"]
    source = (ROOT / "app/modules/event/repo.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    repo = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EventRepo")
    methods = {n.name: ast.unparse(n) for n in repo.body if isinstance(n, ast.AsyncFunctionDef)}
    for name in ("count", "page", "outcome_counts", "latencies", "daily"):
        assert "await self._filter(" in methods[name]
    assert "await self._governed(stmt)" in methods["get_with_type_code"]
    scopes = (ROOT / "app/modules/dynamic/row_scope.py").read_text(encoding="utf-8")
    assert scopes.index("statement = await scope(") < scopes.index("await ensure_governed_tables(session)")
    delivery = (ROOT / "app/modules/event/delivery.py").read_text(encoding="utf-8")
    assert delivery.index("await delivery_payload(session, event, delivery)") < delivery.index(
        "matched = filter_matches("
    )


async def fake_checks():
    session = SimpleNamespace(info={"ontology_id": 1, "data_purpose": "p"})
    user = SimpleNamespace(id="U", ontology_id=1)
    definition = {
        "resources": [{"resource": identity("rule", "R"), "purposes": ["p"], "mask": "null"}],
        "grants": [
            {
                "role": "ontology:owner",
                "purposes": ["p"],
                "classes": [],
                "operations": ["read"],
                "unmask": False,
            }
        ],
    }
    graph = {
        "nodes": [{"id": identity("rule", "R")}],
        "edges": [],
        "unresolved": [],
        "originVersions": {"rule": {"R": "3"}, "action": {}, "event": {}},
        "eventTypes": {},
        "metadataWatermark": datetime(2026, 11, 3, tzinfo=UTC),
    }
    with patch.object(dynamic_guard, "load_definition", AsyncMock(return_value={} | {"resources": []})):
        assert await dynamic_guard.context(session, None) is None
    with (
        patch.object(dynamic_guard, "load_definition", AsyncMock(return_value=definition)),
        patch.object(dynamic_guard, "policy_graph", AsyncMock(return_value=graph)),
        patch.object(dynamic_guard, "policy_roles", AsyncMock(return_value={"ontology:owner"})),
        patch(
            "app.modules.versioning.repo.ReleaseRepo.latest", AsyncMock(return_value=SimpleNamespace(id=9))
        ),
    ):
        ctx = await dynamic_guard.context(session, user)
        assert ctx["allowed"]["rule"] == {}
        definition["grants"][0]["unmask"] = True
        session.info.pop("governance_runtime")
        ctx = await dynamic_guard.context(session, user)
        assert ctx["allowed"]["rule"] == {"R": "3"}
        session.info["data_purpose"] = "wrong"
        assert (await dynamic_guard.context(session, user))["allowed"]["rule"] == {}
        session.info["data_purpose"] = "p"
        session.scalar = AsyncMock(return_value=None)
        try:
            await dynamic_guard.authorize_record(session, user, Alert, SimpleNamespace(id=1))
        except Forbidden as exc:
            assert exc.code == "GOVERNANCE_DYNAMIC_DENIED"
        else:
            raise AssertionError("unknown origin accepted")
    with patch.object(dynamic_guard, "context", AsyncMock(return_value=None)):
        legacy = await EventRepo(session)._filter(5, None, None)
        assert "created_at >" not in str(legacy)


if __name__ == "__main__":
    compile_checks()
    asyncio.run(fake_checks())
    print("dynamic counts before pagination, origin/version/mask and payload guards passed")
