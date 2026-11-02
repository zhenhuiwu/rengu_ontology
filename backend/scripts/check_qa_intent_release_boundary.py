"""One offline QA intent/identity and stale-event contract; no app, DB, model or worker."""
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sqlalchemy import Column, Integer, MetaData, String, Table, select

from app.modules.event import delivery as events
from app.modules.event.models import Event, EventDelivery, EventOutcome
from app.modules.qa import dynamic_ctx as qa
from app.modules.versioning.repo import ReleaseRepo


async def check():
    model = {"type": "inspect", "objKind": "Device", "objId": "16001", "params": {"score": 90}}
    mentioned = [{"kind": "Device", "id": "16002", "label": "device two"}]
    requested, conflict = qa.requested_action("Inspect Device 16002 score 90", model, mentioned)
    assert not conflict and requested["objId"] == "16002" and model["objId"] == "16001"
    alert = {"id": "AL-X", "objKind": "Device", "objId": "16001"}
    assert qa.decision_target("Inspect 16002", [alert], mentioned[0], requested) == {"objectTypeId": "Device", "objectId": "16002"}
    assert qa.requested_action("Inspect device", model, [])[1]
    assert qa.requested_action("Inspect 16001 and 16002", model, mentioned + [{"kind": "Device", "id": "16001", "label": "one"}])[1]
    assert qa.requested_action("Inspect 16002", model, mentioned + [{"kind": "Other", "id": "16002", "label": "other"}])[1]
    assert qa.decision_conflict(requested, model)
    assert qa.decision_conflict(requested, {**requested, "params": {"score": 80}})
    assert qa.decision_conflict({**requested, "params": {"score": True}}, {**requested, "params": {"score": 1}})
    assert qa.decision_conflict(requested, {**requested, "params": {"score": 90, "extra": 1}}) is None

    table = Table("inert_device", MetaData(), Column("id", Integer), Column("code", String), Column("name", String), Column("visible", Integer))
    user = object()

    class Reader:
        async def execute(self, statement):
            sql = str(statement)
            assert "visible" in sql and "__id" in sql
            return [(16002, "device two")]

    class Bound:
        out = ["id", "code", "name"]

        async def stmt(self, viewer):
            assert viewer is user
            return select(table.c.id.label("__id"), table.c.code, table.c.name).where(table.c.visible == 1)

        @asynccontextmanager
        async def read(self, session):
            yield Reader()

    with patch("app.common.sql_guard.ensure_governed_tables", AsyncMock()), patch.object(qa, "_gov", return_value={"inert_device": NS(object_type="Device")}), patch("app.modules.qa.explore_api._bind", AsyncMock(return_value=(None, Bound()))):
        assert await qa._codes_in(object(), ["16002"], user) == {("Device", "16002"): "device two"}
    visible = select(table.c.id.label("__id"), table.c.name).subquery()
    assert qa._label_col(visible, ["id"]).name == "__id", "Hidden label must not be returned"

    sub = {"name": "inert-rule", "kind": "规则", "status": "启用", "ruleId": "R-X"}
    now = datetime.now(UTC)
    event = NS(id=1, event_type_id=1, obj_kind="Device", object_id="16002", ontology_id=16, is_test=False,
               ingested_at=now, envelope_hash="inert", payload={}, release_id=10,
               definition_snapshot={"id": "ET-X", "version": "v1", "objKind": "Device", "payload": [], "subscribers": [sub]})
    delivery = NS(id=1, event_id=1, state="pending", next_at=None, created_at=now, attempts=0, error=None, result=None, subscription=sub, subscriber_name="inert-rule")

    class Session:
        info = {"ontology_id": 16}

        def __init__(self):
            self.calls = 0
            self.records = []

        async def get(self, model, identity):
            return delivery if model is EventDelivery else event

        async def scalar(self, statement):
            self.calls += 1
            return delivery if self.calls == 1 else object()

        async def scalars(self, statement):
            return [delivery]

        def add(self, record):
            self.records.append(record)

    session = Session()
    with patch.object(events, "get_adapter", return_value=NS(xact_lock=AsyncMock())), patch.object(events, "route_for", return_value={}), patch.object(ReleaseRepo, "latest", AsyncMock(return_value=NS(id=11))):
        assert await events.prepare(session, 1) is False
    assert delivery.state == "failed" and delivery.error == "EVENT_RELEASE_CHANGED_REQUIRES_REVIEW"
    assert delivery.attempts == 0 and delivery.next_at is None
    assert event.outcome == EventOutcome.failed and "失败/未知 1，跳过 0" in event.summary
    assert len(session.records) == 1 and session.records[0].kind == "preflight" and session.records[0].error == delivery.error
    assert event.release_id == 10, "Original event snapshot must remain unchanged"
    delivery.state, delivery.error, delivery.result, delivery.next_at = "pending", None, None, None
    session = Session()
    with patch.object(events, "get_adapter", return_value=NS(xact_lock=AsyncMock())), patch.object(events, "route_for", return_value={"rule_code": "R-X", "user": "inert-user"}), patch.object(ReleaseRepo, "latest", AsyncMock(return_value=NS(id=11))):
        assert await events.prepare(session, 1) is False
    assert delivery.error == "EVENT_RELEASE_CHANGED_REQUIRES_REVIEW" and len(session.records) == 1
    root = Path(__file__).resolve().parents[1]
    for path in ("qa/chain.py", "qa/router.py", "qa/dynamic_ctx.py", "event/delivery.py"):
        source = (root / "app/modules" / path).read_text(encoding="utf-8")
        compile(source, path, "exec")
        if path == "qa/chain.py":
            assert "if dyn.wants_advice(question)" not in source
        if path == "qa/router.py":
            assert '"decisionStatus") == "clarification_required"' in source
    print("QA explicit intent, governed identity and stale-event preflight passed (offline only).")


if __name__ == "__main__":
    asyncio.run(check())
