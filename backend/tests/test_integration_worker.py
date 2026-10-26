"""Committed state survives a fresh process and real Procrastinate worker consumption."""

import asyncio
import os
import subprocess
import sys
import uuid

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.dynamic.models import ActionRun, ActionStatus, SourceType
from app.modules.integration.models import (
    ExtSystem,
    ExtSystemStatus,
    ReconcileState,
    ReconcileTask,
    WritebackCall,
)
from app.modules.integration.reconcile import DatabaseReconcileScheduler, lock_run, process
from tests.test_integration_reconcile import Query


async def test_fresh_real_worker_consumes_durable_task(engine):
    token = "worker-" + uuid.uuid4().hex[:16]
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            system = ExtSystem(
                code=token,
                name=token,
                endpoint="https://never-request.invalid",
                idempotent=True,
                timeout_ms=1000,
                status=ExtSystemStatus.ok,
                env="测试",
            )
            session.add(system)
            run = ActionRun(
                code=token,
                action_type_code=token,
                action_name=token,
                source_type=SourceType.manual,
                source_label="手动",
                initiator="test",
                idem_key=token,
                sys_code=token,
                status=ActionStatus.unknown,
                submitted_at=clock.now(),
            )
            session.add(run)
            await session.flush()
            task = await DatabaseReconcileScheduler(session).ensure(await lock_run(session, token))
            task.next_at = clock.now()
        script = """
import asyncio, sys
from app.modules.integration.worker import app, QUEUE
from app.db.session import dispose_engine
assert set(app.tasks) == {
    'action.reconcile', 'builtin:procrastinate.builtin_tasks.remove_old_jobs',
    'procrastinate.builtin_tasks.remove_old_jobs',
}
assert [(p.periodic_id, p.cron) for p in app.periodic_registry.periodic_tasks.values()] == [
    ('action.reconcile', '*/15 * * * *'),
]
async def main():
    try:
        async with app.open_async():
            row = await app.connector.execute_query_one_async(
                query="SELECT to_regclass('procrastinate_jobs') IS NOT NULL AS installed"
            )
            if not row['installed']:
                await app.schema_manager.apply_schema_async()
            job_id = await app.configure_task('action.reconcile').defer_async(timestamp=0)
            await app.run_worker_async(
                queues=[QUEUE], concurrency=1, wait=False,
                listen_notify=False, install_signal_handlers=False,
            )
            row = await app.connector.execute_query_one_async(
                query='SELECT status::text AS status FROM procrastinate_jobs WHERE id=%(id)s', id=job_id
            )
            assert row['status'] == 'succeeded', row
            print(job_id)
    finally:
        await dispose_engine()
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
"""
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env={**os.environ, "RENGU_INTEGRATION_REAL_CONNECT": "false"},
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        async with AsyncSession(engine) as session:
            task = await session.scalar(
                select(ReconcileTask)
                .join(ActionRun, ActionRun.id == ReconcileTask.action_run_id)
                .where(ActionRun.code == token)
            )
            assert (
                task.state == ReconcileState.manual
                and task.last_error == "RECONCILE_UNAVAILABLE"
                and task.tries == 0
            )
            assert not await session.scalar(select(WritebackCall.id).where(WritebackCall.sys_id == system.id))
    finally:
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                delete(ReconcileTask).where(
                    ReconcileTask.action_run_id.in_(select(ActionRun.id).where(ActionRun.code == token))
                )
            )
            await session.execute(delete(ActionRun).where(ActionRun.code == token))
            await session.execute(delete(ExtSystem).where(ExtSystem.code == token))


async def test_two_workers_serialize_query_by_execution_lock(engine):
    token = "locked-" + uuid.uuid4().hex[:16]
    entered, release = asyncio.Event(), asyncio.Event()

    class PausedQuery(Query):
        async def query_by_key(self, key):
            entered.set()
            await release.wait()
            return await super().query_by_key(key)

    query = PausedQuery()
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            system = ExtSystem(
                code=token,
                name=token,
                endpoint="https://never-request.invalid",
                idempotent=True,
                timeout_ms=1000,
                status=ExtSystemStatus.ok,
                env="测试",
            )
            session.add(system)
            run = ActionRun(
                code=token,
                action_type_code=token,
                action_name=token,
                source_type=SourceType.manual,
                source_label="手动",
                initiator="test",
                idem_key=token,
                sys_code=token,
                status=ActionStatus.unknown,
                submitted_at=clock.now(),
            )
            session.add(run)
            await session.flush()
            task = await DatabaseReconcileScheduler(session).ensure(await lock_run(session, token))
            task.next_at = clock.now()
        async with (
            AsyncSession(engine, expire_on_commit=False) as first,
            AsyncSession(engine, expire_on_commit=False) as second,
        ):
            pid1 = await first.scalar(text("SELECT pg_backend_pid()"))
            pid2 = await second.scalar(text("SELECT pg_backend_pid()"))
            jobs = [asyncio.create_task(process(first, token, None, lambda _: query))]
            try:
                await asyncio.wait_for(entered.wait(), 5)
                jobs.append(asyncio.create_task(process(second, token, None, lambda _: query)))
                async with AsyncSession(engine) as observer, asyncio.timeout(5):
                    while pid1 not in await observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": pid2}
                    ):
                        if jobs[1].done():
                            await jobs[1]
                            raise AssertionError("Second worker did not wait for the execution lock")
                release.set()
                await asyncio.wait_for(jobs[0], 5)
                await first.commit()
                await asyncio.wait_for(jobs[1], 5)
                await second.commit()
                assert query.calls == [token]
                assert (
                    await second.scalar(select(ReconcileTask).where(ReconcileTask.action_run_id == run.id))
                ).tries == 1
            finally:
                release.set()
                for job in jobs:
                    if not job.done():
                        job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
    finally:
        async with AsyncSession(engine) as session, session.begin():
            ids = select(ActionRun.id).where(ActionRun.code == token)
            await session.execute(delete(WritebackCall).where(WritebackCall.action_run_id.in_(ids)))
            await session.execute(delete(ReconcileTask).where(ReconcileTask.action_run_id.in_(ids)))
            await session.execute(delete(ActionRun).where(ActionRun.code == token))
            await session.execute(delete(ExtSystem).where(ExtSystem.code == token))
