"""Dedicated subscriber queue: python -m app.modules.event.worker."""

import argparse
import asyncio
import logging
import sys

import procrastinate

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.db.session import dispose_engine, get_sessionmaker
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.event.delivery import discover, due, execute, prepare, recover

QUEUE = "event-delivery"
logger = logging.getLogger(__name__)


async def sweep(timestamp: int = 0) -> int:
    sessions = get_sessionmaker()
    async with sessions() as session, session.begin():
        await recover(session)
        await discover(session)
        ids = await due(session)
    for delivery_id in ids:
        try:
            async with sessions() as session, session.begin():
                claimed = await prepare(session, delivery_id)
            if claimed:
                async with sessions() as session, session.begin():
                    await execute(session, delivery_id)
        except Exception:
            # Exception text from network/database drivers can include credentials or payloads.
            logger.error("Event delivery transaction interrupted: delivery %s", delivery_id)
    return len(ids)


def build_app() -> procrastinate.App:
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    worker = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    task = worker.task(name="event.deliver", queue=QUEUE, lock="event.deliver")(sweep)
    worker.periodic(cron="* * * * *", periodic_id="event.deliver")(task)
    return worker


app = build_app()


async def main(install_schema: bool = False) -> None:
    try:
        async with app.open_async():
            if install_schema:
                row = await app.connector.execute_query_one_async(
                    query="SELECT to_regclass('procrastinate_jobs') IS NOT NULL AS installed"
                )
                if not row["installed"]:
                    await app.schema_manager.apply_schema_async()
                return
            await app.run_worker_async(queues=[QUEUE], concurrency=1, shutdown_graceful_timeout=20)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="处理明确入站事件的订阅任务；默认不启用HTTP发送")
    parser.add_argument("--install-schema", action="store_true")
    arguments = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(arguments.install_schema))
