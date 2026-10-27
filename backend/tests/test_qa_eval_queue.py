"""Procrastinate真实schema/双连接事务/独立worker消费；模型只用FakeLLM。"""

import asyncio
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.auth import resolve_user
from app.common.context import ViewContext
from app.core import clock
from app.core.errors import AppError
from app.db import session as db_session
from app.modules.admin.models import SysRole, SysRoleMember, SysUser
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding
from app.modules.qa.eval import job, queue, worker
from app.modules.qa.llm import FakeLLM
from app.modules.qa.models import QaEvalQuestion, QaEvalRun, QaSession, RunStatus
from app.modules.qa.router import start_run
from app.modules.qa.schemas import EvalRunIn
from app.modules.static.models import Metric, MetricStatus


@pytest.fixture(scope="module")
async def engine():
    from alembic import command
    from alembic.config import Config

    from tests.conftest import _ensure_db

    await _ensure_db()
    config = Config("alembic.ini")
    config.attributes["database_url"] = os.environ["RENGU_DATABASE_URL"]
    await asyncio.to_thread(command.upgrade, config, "head")
    engine = create_async_engine(os.environ["RENGU_DATABASE_URL"])
    yield engine
    await engine.dispose()


@pytest.fixture
async def durable(engine, monkeypatch):
    import procrastinate

    from app.modules.qa import llm as llm_module

    token = "durable-" + uuid4().hex[:12]
    from app.common.ontology import DEFAULT_ONTOLOGY_ID

    # ontology_0018 起领域表强制 RLS：这里自建的会话也落在默认空间（after_begin 按 info 设 rengu.ontology_id）
    factory = async_sessionmaker(engine, expire_on_commit=False, info={"ontology_id": DEFAULT_ONTOLOGY_ID})
    monkeypatch.setattr(db_session, "get_engine", lambda: engine)
    monkeypatch.setattr(db_session, "get_sessionmaker", lambda: factory)
    monkeypatch.setattr(db_session, "get_qa_readonly_engine", lambda: engine)
    fake = FakeLLM('select 1 as "持久队列指标"')
    monkeypatch.setattr(llm_module, "get_llm", lambda: fake)
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(
            conninfo=engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        )
    )
    app.task(name=queue.TASK, queue=queue.QUEUE, lock=queue.LOCK)(worker.run)
    async with app.open_async():
        async with engine.connect() as c:
            installed = await c.scalar(text("SELECT to_regclass('procrastinate_jobs') IS NOT NULL"))
        if not installed:
            await app.schema_manager.apply_schema_async()
        await app.connector.execute_query_async(query=worker.GRANT_RUNTIME)  # 同 --install-schema
        async with factory() as s:
            user = SysUser(code=token, name=token)
            role = SysRole(
                code=token,
                name=token,
                func={"qa-tuning": "manage"},
                objs=[],
                changed_at=clock.now(),
                changed_by="test",
            )
            s.add_all([user, role])
            await s.flush()
            s.add(SysRoleMember(role_id=role.id, user_id=user.id, seq=0))
            s.add(OntologyMember(ontology_id=DEFAULT_ONTOLOGY_ID, user_id=user.id, role="editor"))
            s.add(OntologyRoleBinding(ontology_id=DEFAULT_ONTOLOGY_ID, role_id=role.id))
            s.add(
                QaSession(
                    code=token,
                    owner_id=user.id,
                    title="持久评测",
                    last_at=clock.now(),
                    scope="实际会话范围",
                    caliber="实际会话口径",
                )
            )
            s.add(
                Metric(
                    code=token,
                    api_name=token,
                    display_name="持久队列指标",
                    metric_group="销售",
                    unit="个",
                    version="v1",
                    status=MetricStatus.published,
                    owner=token,
                    formula="1",
                )
            )
            s.add(
                QaEvalQuestion(
                    code=token,
                    question=f"持久测试 {token}",
                    group_name="销售",
                    expect={"metric": token, "dims": [], "time": "上周"},
                )
            )
            await s.commit()
            actor = await resolve_user(s, token)
        try:
            yield factory, app, actor, token, fake
        finally:
            async with factory() as c, c.begin():  # 带空间范围，否则 RLS 下删不到
                codes = select(QaEvalRun.code).where(QaEvalRun.operator == token)
                await c.execute(
                    text(
                        "DELETE FROM procrastinate_jobs WHERE args->>'run_code' IN "
                        "(SELECT code FROM qa_eval_run WHERE operator=:token)"
                    ),
                    {"token": token},
                )
                await c.execute(delete(QaEvalRun).where(QaEvalRun.code.in_(codes)))
                await c.execute(delete(QaEvalQuestion).where(QaEvalQuestion.code == token))
                await c.execute(delete(Metric).where(Metric.code == token))
                await c.execute(delete(QaSession).where(QaSession.code == token))
                uid = select(SysUser.id).where(SysUser.code == token).scalar_subquery()
                await c.execute(delete(OntologyMember).where(OntologyMember.user_id == uid))
                await c.execute(delete(SysUser).where(SysUser.code == token))
                await c.execute(delete(SysRole).where(SysRole.code == token))


async def _request(session, actor, token, fake):
    return await start_run(
        EvalRunIn(group="销售", session_id=token), session, actor, ViewContext(), fake, queue.defer
    )


async def test_queue_and_run_commit_or_rollback_together_then_real_worker_consumes(durable):
    factory, app, actor, token, fake = durable
    async with factory() as s:
        response = await _request(s, actor, token, fake)
        code = response.id
        async with factory() as other:
            assert await other.scalar(select(QaEvalRun.id).where(QaEvalRun.code == code)) is None
            assert (
                await other.scalar(
                    text("SELECT count(*) FROM procrastinate_jobs WHERE args->>'run_code'=:code"),
                    {"code": code},
                )
                == 0
            )
        await s.rollback()
    async with factory() as s:
        response = await _request(s, actor, token, fake)
        await s.commit()
        saved = await s.scalar(select(QaEvalRun).where(QaEvalRun.code == response.id))
        assert saved.status == RunStatus.queued and saved.queue_job_id is not None and not fake.prompts
        assert saved.execution_context["scope"] == "实际会话范围"
        assert saved.execution_context["caliber"] == "实际会话口径"
    # API请求/事务已经结束；只有独立Procrastinate worker启动后才开始执行。
    async with job.worker_lifespan():
        await app.run_worker_async(
            queues=[queue.QUEUE],
            concurrency=2,
            wait=False,
            listen_notify=False,
            install_signal_handlers=False,
        )
    async with factory() as s:
        saved = await s.scalar(select(QaEvalRun).where(QaEvalRun.code == response.id))
        assert saved.status == RunStatus.done and saved.passed == saved.total == 1
        assert (
            await s.scalar(
                text("SELECT status::text FROM procrastinate_jobs WHERE id=:id"), {"id": saved.queue_job_id}
            )
            == "succeeded"
        )
    prompt = fake.prompts[0][0]["content"]
    assert "实际会话范围" in prompt and "实际会话口径" in prompt


async def test_concurrent_start_is_database_global_singleton(durable):
    factory, _, actor, token, fake = durable
    async with factory() as first, factory() as second:
        response = await _request(first, actor, token, fake)
        pid = await second.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(_request(second, actor, token, fake))
        async with asyncio.timeout(5):
            while not await first.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks "
                    "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
                ),
                {"pid": pid},
            ):
                if task.done():
                    await task
                    pytest.fail("second request did not wait for global transaction lock")
        await first.commit()
        with pytest.raises(AppError) as error:
            await task
        assert error.value.code == "QA_EVAL_BUSY" and error.value.details["id"] == response.id
        await second.rollback()


async def test_recovery_fails_orphan_and_queue_job_without_losing_progress(durable):
    factory, app, actor, token, fake = durable
    async with factory() as s:
        response = await _request(s, actor, token, fake)
        await s.commit()
    worker_id = await app.job_manager.register_worker()
    fetched = await app.job_manager.fetch_job(queues=[queue.QUEUE], worker_id=worker_id)
    assert fetched is not None
    try:
        async with factory() as s:
            run = await s.scalar(select(QaEvalRun).where(QaEvalRun.code == response.id))
            run.status, run.worker_id, run.passed = RunStatus.running, "f" * 32, 1
            await s.commit()
        assert await job.recover_interrupted_runs() == 1
        assert await job.recover_interrupted_runs() == 0
        await job._background(response.id)  # 迟到交付不能把失败改回运行中或完成。
        async with factory() as s:
            run = await s.scalar(select(QaEvalRun).where(QaEvalRun.code == response.id))
            assert run.status == RunStatus.failed and run.passed == 1
            assert "已保留" in run.note
            assert (
                await s.scalar(
                    text("SELECT status::text FROM procrastinate_jobs WHERE id=:id"), {"id": run.queue_job_id}
                )
                == "failed"
            )
    finally:
        await app.job_manager.unregister_worker(worker_id)


async def test_fresh_worker_process_bootstraps_models_without_http_app(durable):
    script = """
import asyncio, sys
from sqlalchemy.orm import configure_mappers
from app.modules.qa.eval.worker import app
from app.modules.qa.eval import job
from app.db.session import dispose_engine
configure_mappers()
assert 'app.main' not in sys.modules
names = {
    name for name in app.tasks
    if not name.removeprefix('builtin:').startswith('procrastinate.builtin_tasks.')
}
assert names == {'qa.eval.run', 'qa.eval.generate', 'qa.eval.daily'}
assert {k for k, _ in app.periodic_registry.periodic_tasks} == {'qa.eval.generate', 'qa.eval.daily'}
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
async def main():
    try:
        async with app.open_async(), job.worker_lifespan():
            await job.recover_interrupted_runs()
            await app.run_worker_async(queues=['qa-eval'], concurrency=1, wait=False, listen_notify=False,
                install_signal_handlers=False)
    finally:
        await dispose_engine()
asyncio.run(main())
print('fresh worker started and stopped')
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-X", "utf8", "-c", script],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "fresh worker started and stopped" in result.stdout
