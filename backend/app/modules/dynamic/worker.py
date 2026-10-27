"""Dedicated opt-in scheduler: python -m app.modules.dynamic.worker."""

import asyncio
import sys

import procrastinate

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.db.session import dispose_engine
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.dynamic.scheduler import sweep

QUEUE = "rules"


def build_app():
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    app = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    task = app.task(name="rule.sweep", queue=QUEUE, lock="rule.sweep")(sweep)
    app.periodic(cron="* * * * *", periodic_id="rule.sweep")(task)
    return app


app = build_app()


async def main():
    try:
        # A restart immediately compensates eligible persisted slots before the next periodic tick.
        await sweep()
        async with app.open_async():
            await app.run_worker_async(queues=[QUEUE], concurrency=1, shutdown_graceful_timeout=5)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
