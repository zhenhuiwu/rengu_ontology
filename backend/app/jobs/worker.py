"""procrastinate 接入：把 app.jobs.JOBS 全部注册成任务，周期任务配 periodic。

    uv run procrastinate --app=app.jobs.worker.app schema --apply   # 建 procrastinate_* 表（alembic 不管）
    uv run procrastinate --app=app.jobs.worker.app worker

业务里排任务：`await app.configure_task("event.deliver").defer_async(event_id=...)`。
"""

import functools
from collections.abc import Awaitable, Callable
from typing import Any

import procrastinate

from app.core.config import get_settings
from app.jobs import JOBS, JobSpec


def _conninfo() -> str:
    # 应用用 asyncpg 方言；procrastinate 用 psycopg3，去掉方言后缀即可
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _periodic_wrapper(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """procrastinate 周期任务会传 timestamp；业务函数签名不带它。"""

    @functools.wraps(fn)
    async def run(timestamp: int) -> Any:
        return await fn()

    return run


def register(app: procrastinate.App, jobs: list[JobSpec] = JOBS) -> procrastinate.App:
    for spec in jobs:
        if spec.cron and spec.enabled:
            task = app.task(name=spec.name, queue=spec.queue)(_periodic_wrapper(spec.fn))
            app.periodic(cron=spec.cron, periodic_id=spec.name)(task)
        else:
            app.task(name=spec.name, queue=spec.queue)(spec.fn)
    return app


def build_app() -> procrastinate.App:
    return register(procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=_conninfo())))


app = build_app()
