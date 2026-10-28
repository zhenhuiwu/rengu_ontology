"""Automatic suggestions recheck locked real PG candidates and never persist raw driver failures."""

from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError

from app.core import clock
from app.core.errors import AppError
from app.modules.dynamic import auto_decision
from app.modules.dynamic.models import ActionRun, Alert, AlertStatus, DecisionLog
from tests.dynamic_support import dyn  # noqa: F401


class SessionView:
    """Keep each production transaction a real SAVEPOINT inside this rollback-only test."""

    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    def begin(self):
        return self.session.begin_nested()


def decision(alert, code="PRIOR-DECISION"):
    return DecisionLog(
        code=code,
        caller_kind="user",
        caller_id="U010245",
        obj_kind=alert.obj_kind,
        obj_id=alert.obj_id,
        alert_code=alert.code,
        input={},
        output={},
    )


@pytest.fixture
async def pending(dyn, monkeypatch):  # noqa: F811
    alert = await dyn.scalar(select(Alert).where(Alert.rule_code == "R-012").order_by(Alert.id).limit(1))
    alert.is_seed, alert.created_by, alert.triggered_at = False, "U010245", clock.now()
    alert.status = AlertStatus.open
    await dyn.flush()
    state = {"calls": 0, "hook": None}

    @asynccontextmanager
    async def sessions():
        state["calls"] += 1
        if state["hook"]:
            await state["hook"](state["calls"])
        yield SessionView(dyn)

    def factory(ontology_id):
        assert ontology_id == 1
        return sessions

    monkeypatch.setattr(auto_decision, "scoped_sessions", factory)
    return dyn, alert, state


@pytest.mark.parametrize("change", ["closed", "already_evaluated", "expired", "future"])
async def test_stale_candidate_not_evaluated(pending, monkeypatch, change):
    session, alert, state = pending
    attempts = []

    async def update_after_scan(call):
        if call != 2:
            return
        if change == "closed":
            alert.status = AlertStatus.resolved
        elif change == "already_evaluated":
            session.add(decision(alert))
        else:
            alert.triggered_at = clock.now() + (
                timedelta(hours=1) if change == "future" else -timedelta(hours=25)
            )
        await session.flush()

    async def must_not_evaluate(*args):
        attempts.append(True)
        raise AssertionError("stale candidate must not reach the evaluator")

    state["hook"] = update_after_scan
    monkeypatch.setattr(auto_decision, "decide", must_not_evaluate)
    assert await auto_decision.decide_pending(1, limit=1) == 0
    assert attempts == []
    count = await session.scalar(
        select(func.count()).select_from(DecisionLog).where(DecisionLog.alert_code == alert.code)
    )
    assert count == int(change == "already_evaluated")


async def test_legal_candidate_real_evaluator_is_locked_and_not_repeated(pending, engine):
    session, alert, _ = pending
    before = await session.scalar(select(func.count()).select_from(ActionRun))
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        assert await auto_decision.decide_pending(1, limit=1) == 1
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    rows = list(await session.scalars(select(DecisionLog).where(DecisionLog.alert_code == alert.code)))
    assert len(rows) == 1 and rows[0].caller_kind == "system"
    assert rows[0].output["recommendations"]
    assert any("FOR UPDATE SKIP LOCKED" in statement for statement in statements)
    assert await auto_decision.decide_pending(1, limit=1) == 0
    assert await session.scalar(select(func.count()).select_from(ActionRun)) == before


@pytest.mark.parametrize("business_error", [False, True])
async def test_failure_stays_generic_and_is_not_retried(pending, monkeypatch, caplog, business_error):
    session, alert, _ = pending
    marker = "PRIVATE_DRIVER_PARAMETER"
    attempts = []

    async def fail(*args):
        attempts.append(True)
        if business_error:
            raise AppError(marker, code="NO_PERMISSION", status_code=403)
        raise DBAPIError("SELECT private_column", {"private": marker}, RuntimeError(marker))

    monkeypatch.setattr(auto_decision, "decide", fail)
    assert await auto_decision.decide_pending(1, limit=1) == 0
    row = await session.scalar(select(DecisionLog).where(DecisionLog.alert_code == alert.code))
    assert row.output["errorCode"] == ("NO_PERMISSION" if business_error else "AUTO_DECISION_FAILED")
    assert marker not in str(row.output) and "SELECT" not in str(row.output)
    assert marker not in caplog.text and "private_column" not in caplog.text
    assert await auto_decision.decide_pending(1, limit=1) == 0 and len(attempts) == 1


async def test_failure_record_does_not_overwrite_intervening_decision(pending, monkeypatch):
    session, alert, state = pending

    async def new_manual_decision(call):
        if call == 3:
            session.add(decision(alert))
            await session.flush()

    async def fail(*args):
        raise RuntimeError("not persisted")

    state["hook"] = new_manual_decision
    monkeypatch.setattr(auto_decision, "decide", fail)
    assert await auto_decision.decide_pending(1, limit=1) == 0
    rows = list(await session.scalars(select(DecisionLog).where(DecisionLog.alert_code == alert.code)))
    assert len(rows) == 1 and rows[0].code == "PRIOR-DECISION"
