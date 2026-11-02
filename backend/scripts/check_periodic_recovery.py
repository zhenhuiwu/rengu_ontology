"""One offline recovery/fair-selection contract check; no DB, worker, HTTP or app startup."""

import ast
import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, case, func, or_, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("periodic_recovery_check", ROOT / "app/jobs/recovery.py")
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)
logging.getLogger(recovery.__name__).addHandler(logging.NullHandler())
logging.getLogger(recovery.__name__).propagate = False
NOW = datetime(2026, 11, 2, tzinfo=UTC)


class Connection:
    def __init__(self, owner):
        self.owner, self.held = owner, set()

    async def execute(self, query, parameters):
        if "pg_advisory_unlock" in query:
            self.held.remove(parameters[0])
        else:
            self.held.add(parameters[0])

    async def commit(self): pass
    async def rollback(self): pass
    async def close(self): raise AssertionError("A clean ownership lock must be released normally")

    @asynccontextmanager
    async def transaction(self):
        yield


class Connector:
    def __init__(self, rows):
        self.rows, self.finished, self.live = rows, [], set()
        self.connection = Connection(self)
        self.pool = SimpleNamespace(connection=self.connect)

    @asynccontextmanager
    async def connect(self):
        yield self.connection

    async def execute_query_all_async_with_connection(self, connection, *, query, **values):
        if "FROM procrastinate_workers" in query:
            row = next((r for r in self.rows.values() if r["worker_id"] == values["owner"]), None)
            return [{"last_heartbeat": row["last_heartbeat"]}] if row and row["worker_id"] else []
        row = self.rows[values["id"]]
        if "worker_id IS NOT DISTINCT" in query:
            return [{"id": row["id"]}] if row["status"] == "doing" and row["worker_id"] == values["owner"] else []
        assert "FOR UPDATE OF j SKIP LOCKED" in query
        return [dict(row)]

    async def execute_query_one_async_with_connection(self, connection, *, query, **values):
        if "pg_try_advisory_xact_lock" in query:
            return {"acquired": values["key"] not in self.live}
        assert "procrastinate_finish_job_v1" in query and "'failed', false" in query
        row = self.rows[values["id"]]
        assert row["status"] == "doing"
        row["status"] = "failed"
        self.finished.append(row["id"])
        return {}


async def check_recovery():
    base = dict(queue_name="event-delivery", task_name="event.deliver", args={"timestamp": 1},
                status="doing", checked_at=NOW, started_at=NOW - timedelta(hours=1))
    rows = {i: {**base, "id": i, "worker_id": i, "last_heartbeat": NOW - timedelta(minutes=5)}
            for i in range(1, 11)}
    rows[2]["last_heartbeat"] = NOW  # A stale API snapshot must not override a refreshed heartbeat.
    rows[4]["task_name"] = "unknown.operation"
    rows[5]["args"] = {"event_id": "do-not-replay"}
    rows[6]["worker_id"] = None
    rows[7].update(worker_id=None, started_at=None)
    rows[8]["status"] = "todo"
    rows[9]["last_heartbeat"] = NOW + timedelta(minutes=5)
    rows[10]["queue_name"] = "foreign-queue"
    connector = Connector(rows)
    connector.live.add(recovery.ownership_key(3))

    async def stale_snapshots(**kwargs):
        assert kwargs["seconds_since_heartbeat"] == 120
        return [SimpleNamespace(id=i) for i in rows]

    app = SimpleNamespace(connector=connector, job_manager=SimpleNamespace(get_stalled_jobs=stale_snapshots),
                          periodic_registry=SimpleNamespace(periodic_tasks={
                              "registered": SimpleNamespace(task=SimpleNamespace(queue="event-delivery", name="event.deliver")),
                          }))
    assert await recovery.recover_periodic(app, ["event-delivery"]) == 2
    assert connector.finished == [1, 6]
    assert len(rows) == 10 and all(rows[i]["status"] == "doing" for i in [2, 3, 4, 5, 7, 9, 10])
    assert await recovery.recover_periodic(app, ["unrelated"]) == 0

    context = SimpleNamespace(app=app, job=SimpleNamespace(id=2, queue="event-delivery", task_name="event.deliver", worker_id=2))
    called = []

    async def business():
        assert recovery.ownership_key(2) in connector.connection.held
        called.append(True)
        return "controlled"

    assert await recovery.periodic_ownership(business, context, None) == "controlled"
    assert called == [True] and not connector.connection.held
    rows[2]["status"] = "failed"
    try:
        await recovery.periodic_ownership(business, context, None)
    except RuntimeError:
        pass
    else:
        raise AssertionError("A late worker must not run after the queue ownership changed")
    assert called == [True] and not connector.connection.held


Base = declarative_base()


class Event(Base):
    __tablename__ = "event"
    id = Column(BigInteger, primary_key=True)
    event_type_id = Column(BigInteger)


class Delivery(Base):
    __tablename__ = "event_delivery"
    id = Column(BigInteger, primary_key=True)
    event_id = Column(BigInteger)
    subscriber_name = Column(String)
    state = Column(String)
    next_at = Column(DateTime(timezone=True))
    started_at = Column(DateTime(timezone=True))


class Outbox(Base):
    __tablename__ = "outbox_event"
    id = Column(BigInteger, primary_key=True)
    topic = Column(String)
    payload = Column(JSONB)
    attempts = Column(Integer)
    available_at = Column(DateTime(timezone=True))
    dispatched_at = Column(DateTime(timezone=True))


def source_function(path, name, *, stop_before_loop=False):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    node.body = [n for n in node.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    if stop_before_loop:
        node.body = node.body[:next(i for i, n in enumerate(node.body) if isinstance(n, ast.For))]
    namespace = dict(select=select, func=func, case=case, or_=or_, Event=Event, EventDelivery=Delivery,
                     OutboxEvent=Outbox, require_ontology_id=lambda s: 16, execution_now=lambda: NOW,
                     clock=SimpleNamespace(now=lambda: NOW))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


async def check_selection_sql():
    captured = []

    async def capture(statement):
        captured.append(statement)
        return []

    session = SimpleNamespace(scalars=capture)
    await source_function("app/modules/event/delivery.py", "due")(session)
    due = str(captured.pop().compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "PARTITION BY event.event_type_id, event_delivery.subscriber_name" in due
    assert 'ORDER BY anon_1.round, anon_1.last_served ASC NULLS FIRST' in due
    assert "event_delivery.state = 'pending'" in due and "event_delivery.next_at <=" in due
    assert "LIMIT 200" in due and "max(event_delivery.started_at)" in due
    await source_function("app/modules/notifications/service.py", "dispatch_workspace", stop_before_loop=True)(session)
    outbox = str(captured.pop().compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "min(outbox_event.id) FILTER (WHERE outbox_event.dispatched_at IS NULL)" in outbox
    assert "GROUP BY CAST(outbox_event.payload ->> 'pageId'" in outbox
    assert "last_served ASC NULLS FIRST" in outbox and "outbox_event.available_at <=" in outbox
    assert "FOR UPDATE OF outbox_event SKIP LOCKED" in outbox and "LIMIT 100" in outbox
    # Relational examples for the compiled selection contract, without executing SQL or a database.
    high = [(round_, "high", round_) for round_ in range(1, 202)]
    low = [(1, "low", 1000)]
    ordered = sorted(high + low, key=lambda row: (row[0], row[2]))[:200]
    assert low[0] in ordered and high[-1] not in ordered
    heads = {"high": [1, 2, 3], "low": [1000]}
    assert [min(ids) for ids in heads.values()] == [1, 1000]


async def main():
    for path in ["app/jobs/recovery.py", "app/jobs/worker.py", "app/modules/event/worker.py",
                 "app/modules/dynamic/worker.py", "app/modules/data/worker.py",
                 "app/modules/integration/worker.py", "app/modules/qa/eval/worker.py"]:
        ast.parse((ROOT / path).read_text(encoding="utf-8"))
    await check_recovery()
    await check_selection_sql()
    print("Periodic orphan guards and bounded subscription-selection contracts passed (offline; no DB/worker/HTTP).")


if __name__ == "__main__":
    asyncio.run(main())
