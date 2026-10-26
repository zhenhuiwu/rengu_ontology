"""动作日编号的短事务预留，以及纯日志追加的父行锁。"""

import asyncio

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core import clock
from app.modules.dynamic.models import ActionRun, ActionRunDailyCounter, ActionStatus, SourceType
from app.modules.dynamic.repo import ActionRunRepo
from tests.helpers.migrations import temp_database

DAY = "21000101"


@pytest.fixture
async def number_engine():
    # 纯日志需要真实提交；只追加记录不能清理，整库隔离后回收。
    async with temp_database() as (cfg, _):
        await asyncio.to_thread(command.upgrade, cfg, "head")
        engine = create_async_engine(cfg.attributes["database_url"])
        try:
            yield engine
        finally:
            await engine.dispose()


def run(code, *, historical=False):
    return ActionRun(
        code=code,
        action_type_code="test",
        action_name="test",
        source_type=SourceType.manual,
        source_label="手动",
        initiator="test",
        idem_key=code.lower(),
        status=ActionStatus.staged,
        submitted_at=clock.now(),
        in_center=not historical,
    )


async def test_daily_number_history_business_rollback_and_allocator_failure(number_engine, monkeypatch):
    async with AsyncSession(number_engine) as setup:
        setup.add(run(f"ACT-{DAY}-0042", historical=True))
        await setup.commit()
    async with AsyncSession(number_engine) as request:
        repo = ActionRunRepo(request)
        assert await repo.next_code(DAY) == f"ACT-{DAY}-0043"
        await request.rollback()
        assert await repo.next_code(DAY) == f"ACT-{DAY}-0044"  # 主事务回滚不重用已发号。
        assert await repo.next_code("21000102") == "ACT-21000102-0001"
        request.add(run("ACT-21000102-0012"))
        assert await repo.next_code("21000102") == "ACT-21000102-0013"  # 请求内未提交历史也参与下限。
        await request.rollback()

        flush = AsyncSession.flush

        async def fail_after_counter_write(session, *args, **kwargs):
            counter_write = any(isinstance(row, ActionRunDailyCounter) for row in session.new | session.dirty)
            await flush(session, *args, **kwargs)
            if counter_write:
                raise RuntimeError("counter write failed")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "flush", fail_after_counter_write)
            with pytest.raises(RuntimeError, match="counter write failed"):
                await repo.next_code(DAY)
        assert await request.scalar(text("SELECT 1")) == 1
        assert await repo.next_code(DAY) == f"ACT-{DAY}-0045"  # 分配事务自身失败则整体回滚。


async def test_daily_allocator_works_when_request_pool_is_full(number_engine):
    limited = create_async_engine(number_engine.url, pool_size=2, max_overflow=0, pool_timeout=0.1)
    try:
        async with AsyncSession(limited) as first, AsyncSession(limited) as second:
            await first.scalar(text("SELECT 1"))
            await second.scalar(text("SELECT 1"))  # 请求池全部占满，不允许分配器再借主池连接。
            codes = await asyncio.wait_for(
                asyncio.gather(
                    ActionRunRepo(first).next_code(DAY),
                    ActionRunRepo(second).next_code(DAY),
                ),
                5,
            )
            assert sorted(codes) == [f"ACT-{DAY}-0001", f"ACT-{DAY}-0002"]
            first.add(run(codes[0]))
            second.add(run(codes[1]))
            await first.flush()
            await second.flush()
            # 两个业务事务仍未结束（可正在等待外调），当天和次日仍可发号。
            async with AsyncSession(number_engine) as third:
                assert await ActionRunRepo(third).next_code(DAY) == f"ACT-{DAY}-0003"
                assert await ActionRunRepo(third).next_code("21000102") == "ACT-21000102-0001"
    finally:
        await limited.dispose()


async def test_pure_action_logs_lock_parent_and_leave_other_runs_free(number_engine):
    async with AsyncSession(number_engine) as setup:
        setup.add_all([run(f"ACT-{DAY}-0001"), run(f"ACT-{DAY}-0002")])
        await setup.commit()
    async with (
        AsyncSession(number_engine, expire_on_commit=False) as first,
        AsyncSession(number_engine, expire_on_commit=False) as second,
        AsyncSession(number_engine) as other,
    ):
        a = await first.scalar(select(ActionRun).where(ActionRun.code == f"ACT-{DAY}-0001"))
        b = await second.get(ActionRun, a.id)
        pid = await second.scalar(text("SELECT pg_backend_pid()"))
        initial_version = a.lock_version
        log = await ActionRunRepo(first).add_log(a, at=clock.now(), text="first")
        assert log.seq == 1
        task = asyncio.create_task(ActionRunRepo(second).add_log(b, at=clock.now(), text="second"))
        try:
            async with asyncio.timeout(5):
                while not await first.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"),
                    {"pid": pid},
                ):
                    if task.done():
                        await task
                        pytest.fail("纯日志追加没有等待父行锁")
            different = await other.scalar(select(ActionRun).where(ActionRun.code == f"ACT-{DAY}-0002"))
            await asyncio.wait_for(ActionRunRepo(other).add_log(different, at=clock.now(), text="other"), 5)
            await other.rollback()
            await first.commit()
            assert (await asyncio.wait_for(task, 5)).seq == 2
            await second.commit()
            await first.refresh(a)
            assert a.lock_version == initial_version  # 序号保护不刷新或改写父记录业务状态。
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
