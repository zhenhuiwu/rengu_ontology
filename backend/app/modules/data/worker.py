"""Opt-in quality queue: python -m app.modules.data.worker (no source connection or notifications)."""

import asyncio
import sys

import procrastinate

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.db.session import dispose_engine
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.data.quality_scheduler import sweep

QUEUE = "quality"


def build_app():
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    worker = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    task = worker.task(name="quality.sweep", queue=QUEUE, lock="quality.sweep")(sweep)
    worker.periodic(cron="* * * * *", periodic_id="quality.sweep")(task)
    return worker


app = build_app()


async def main():
    try:
        async with app.open_async():
            await app.run_worker_async(queues=[QUEUE], concurrency=1, shutdown_graceful_timeout=5)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
