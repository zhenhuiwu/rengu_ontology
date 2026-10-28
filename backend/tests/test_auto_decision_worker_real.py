"""P1 draft: real worker transactions see committed alerts; no shared seed baseline."""

import asyncio
from types import SimpleNamespace

import pytest
from alembic import command
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.common.ontology import RUNTIME_ROLE, set_ontology_scope
from app.core import clock
from app.db import session as db_session
from app.jobs import scope as job_scope
from app.modules.admin.models import SysRole, SysRoleMember, SysUser
from app.modules.dynamic import auto_decision, scheduler
from app.modules.dynamic.models import ActionRun, Alert, AlertStatus, DecisionLog
from app.modules.dynamic.rules_models import RuleScheduleCursor, RuleScheduleRun
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding
from app.modules.versioning.sections import isolated_sections, load_sections
from app.seeds.loader import run_seeds
from app.seeds.registry import SEED_MODULES
from tests.helpers.migrations import temp_database


@pytest.fixture
async def committed_decision(monkeypatch):
    # Only this helper's newly created random database is seeded/committed/dropped.
    async with temp_database() as (config, _dsn):
        await asyncio.to_thread(command.upgrade, config, "head")
        engine = create_async_engine(config.attributes["database_url"], pool_size=3, max_overflow=0)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(job_scope, "get_sessionmaker", lambda: factory)
        monkeypatch.setattr(db_session, "get_qa_readonly_engine", lambda: engine)
        try:
            with isolated_sections():
                load_sections()
                async with engine.connect() as publisher_connection:
                    async with AsyncSession(bind=publisher_connection, expire_on_commit=False) as publisher:
                        await set_ontology_scope(publisher, 1)
                        await run_seeds(publisher, SEED_MODULES[: SEED_MODULES.index("dynamic") + 1])
                        user = await publisher.scalar(select(SysUser).where(SysUser.code == "U010245"))
                        role = await publisher.scalar(select(SysRole).where(SysRole.code == "R-01"))
                        assert user is not None and role is not None
                        if not await publisher.scalar(
                            select(SysRoleMember.id).where(
                                SysRoleMember.user_id == user.id, SysRoleMember.role_id == role.id
                            )
                        ):
                            publisher.add(SysRoleMember(user_id=user.id, role_id=role.id, seq=0))
                        if not await publisher.scalar(
                            select(OntologyMember.id).where(
                                OntologyMember.ontology_id == 1, OntologyMember.user_id == user.id
                            )
                        ):
                            publisher.add(OntologyMember(ontology_id=1, user_id=user.id, role="owner"))
                        if not await publisher.scalar(
                            select(OntologyRoleBinding.id).where(
                                OntologyRoleBinding.ontology_id == 1, OntologyRoleBinding.role_id == role.id
                            )
                        ):
                            publisher.add(OntologyRoleBinding(ontology_id=1, role_id=role.id))
                        seed = await publisher.scalar(
                            select(Alert).where(Alert.rule_code == "R-012").order_by(Alert.id).limit(1)
                        )
                        assert seed is not None
                        values = {
                            column.name: getattr(seed, column.name)
                            for column in Alert.__table__.c
                            if column.name != "id"
                        }
                        values.update(
                            code="AL-P1-COMMITTED",
                            is_seed=False,
                            created_by=user.code,
                            created_at=clock.now(),
                            updated_at=clock.now(),
                            triggered_at=clock.now(),
                            status=AlertStatus.open,
                            action_run_id=None,
                            assignee=None,
                            dedupe_key="p1-committed",
                            lock_version=1,
                        )
                        publisher.add(Alert(**values))
                        await publisher.commit()
                        assert not publisher_connection.in_transaction(), (
                            "must be a real commit, not a savepoint"
                        )
                    publisher_pid = await publisher_connection.scalar(text("SELECT pg_backend_pid()"))
                    await publisher_connection.rollback()
                    insert_pids = set()

                    def capture(connection, _cursor, statement, _params, _context, _many):
                        if "insert into decision_log" in statement.lower().replace('"', ""):
                            insert_pids.add(connection.connection.driver_connection.get_server_pid())

                    event.listen(engine.sync_engine, "before_cursor_execute", capture)
                    try:
                        case = SimpleNamespace(
                            publisher_pid=publisher_pid, insert_pids=insert_pids, code="AL-P1-COMMITTED"
                        )
                        case.before = await observe(case)
                        assert case.before["pid"] != publisher_pid and case.before["logs"] == []
                        assert case.before["role"] == RUNTIME_ROLE and case.before["scope"] == "1"
                        assert case.before["schedule_rows"] == 0
                        yield case
                    finally:
                        event.remove(engine.sync_engine, "before_cursor_execute", capture)
        finally:
            await engine.dispose()


async def observe(case):
    # Every observation opens a fresh production-scoped session on a physical connection
    # different from the publisher, which stays checked out until the fixture finishes.
    async with job_scope.scoped_sessions(1)() as session:
        alert = (
            (await session.execute(select(Alert.__table__).where(Alert.code == case.code))).mappings().one()
        )
        logs = list(await session.scalars(select(DecisionLog).where(DecisionLog.alert_code == case.code)))
        return {
            "alert": dict(alert),
            "logs": [
                {"code": row.code, "caller_kind": row.caller_kind, "output": row.output} for row in logs
            ],
            "runs": await session.scalar(select(func.count()).select_from(ActionRun)),
            "schedule_rows": (await session.scalar(select(func.count()).select_from(RuleScheduleCursor)))
            + (await session.scalar(select(func.count()).select_from(RuleScheduleRun))),
            "pid": await session.scalar(text("SELECT pg_backend_pid()")),
            "role": await session.scalar(text("SELECT current_user")),
            "scope": await session.scalar(text("SELECT current_setting('rengu.ontology_id')")),
        }


async def test_sweep_tail_commits_real_recommendation_once_without_staging_action(committed_decision):
    case = committed_decision
    # No due rule slots exist: this specifically exercises the real sweep tail.
    assert await scheduler._sweep_workspace(1) == 0
    after = await observe(case)
    assert after["alert"] == case.before["alert"] and after["runs"] == case.before["runs"]
    assert len(after["logs"]) == 1 and after["logs"][0]["caller_kind"] == "system"
    assert after["logs"][0]["output"]["suggestOnly"] is True
    assert after["logs"][0]["output"]["recommendations"], after["logs"][0]["output"]
    assert case.insert_pids and case.publisher_pid not in case.insert_pids
    assert await auto_decision.decide_pending(1, limit=1) == 0
    assert (await observe(case))["logs"] == after["logs"]


async def test_worker_error_record_is_committed_sanitized_and_not_retried(
    committed_decision, monkeypatch, caplog
):
    case = committed_decision
    attempts = []
    marker = "P1_PRIVATE_FAILURE_PARAMETER"

    async def fail(_session, _alert):
        attempts.append(True)
        raise RuntimeError(marker)

    # Only fault injection is replaced; scoped_sessions and failure-record transactions are real.
    monkeypatch.setattr(auto_decision, "decide", fail)
    assert await auto_decision.decide_pending(1, limit=1) == 0
    after = await observe(case)
    assert after["alert"] == case.before["alert"] and after["runs"] == case.before["runs"]
    assert len(after["logs"]) == 1
    assert after["logs"][0]["output"]["errorCode"] == "AUTO_DECISION_FAILED"
    assert after["logs"][0]["output"]["recommendations"] == []
    assert marker not in str(after["logs"]) and marker not in caplog.text
    assert case.insert_pids and case.publisher_pid not in case.insert_pids
    assert await auto_decision.decide_pending(1, limit=1) == 0 and len(attempts) == 1
    assert (await observe(case))["logs"] == after["logs"]
