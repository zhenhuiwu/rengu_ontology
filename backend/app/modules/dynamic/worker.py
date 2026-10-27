"""Dedicated opt-in scheduler: python -m app.modules.dynamic.worker."""

import asyncio
import logging
import sys

import procrastinate

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.db.session import dispose_engine
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.dynamic.scheduler import sweep

QUEUE = "rules"
QA_QUEUE = "qa_pages"  # 问数页面定时任务（任务代码归 QA：app.modules.qa.page_schedule.register）
logger = logging.getLogger(__name__)


def _mount_qa(app) -> None:
    try:
        from app.modules.qa import page_schedule
    except ImportError:
        logger.warning("app.modules.qa.page_schedule not available; qa_pages tasks skipped")
        return
    page_schedule.register(app)


def build_app():
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    task = app.task(name="rule.sweep", queue=QUEUE, lock="rule.sweep")(sweep)
    app.periodic(cron="* * * * *", periodic_id="rule.sweep")(task)
    _mount_qa(app)
    return app


app = build_app()


async def main():
    try:
        # A restart immediately compensates eligible persisted slots before the next periodic tick.
        await sweep()
        async with app.open_async():
            await app.run_worker_async(queues=[QUEUE, QA_QUEUE], concurrency=1, shutdown_graceful_timeout=5)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
