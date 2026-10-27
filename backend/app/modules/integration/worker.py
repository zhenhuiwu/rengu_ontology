"""Dedicated durable reconciliation worker: python -m app.modules.integration.worker."""

import argparse
import asyncio
import logging
import sys

import procrastinate
from sqlalchemy import or_, select

from app.core import clock
from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.modules.biz import governance as _governance  # noqa: F401
from app.db.session import dispose_engine, get_sessionmaker
from app.modules.dynamic.catalog import get_list_catalog
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.dynamic.ports import Ports, UnconfiguredAdapters
from app.modules.dynamic.service import DynamicReconciliationListener
from app.modules.integration.emitter import DatabaseEventEmitter
from app.modules.integration.models import ReconcileState, ReconcileTask
from app.modules.integration.reconcile import DatabaseReconcileScheduler, discover, process

QUEUE = "reconcile"
logger = logging.getLogger(__name__)


async def sweep(timestamp: int = 0) -> int:
    sessions = get_sessionmaker()
    async with sessions() as session, session.begin():
        await discover(session)
        codes = list(
            await session.scalars(
                select(ActionRun.code)
                .join(ReconcileTask, ReconcileTask.action_run_id == ActionRun.id)
                .where(
                    or_(
                        (ReconcileTask.state == ReconcileState.querying)
                        & (ReconcileTask.next_at <= clock.now()),
                        (ReconcileTask.state != ReconcileState.resolved)
                        & (ActionRun.status == ActionStatus.reconciled),
                    )
                )
                .order_by(ReconcileTask.id)
                .limit(200)
            )
        )
    for code in codes:
        try:
            async with sessions() as session, session.begin():
                scheduler = DatabaseReconcileScheduler(session)
                unavailable = UnconfiguredAdapters()
                ports = Ports(unavailable, DatabaseEventEmitter(session), scheduler, unavailable)
                listener = DynamicReconciliationListener(session, await get_list_catalog(session), ports)
                await process(session, code, listener)
        except Exception:
            logger.exception("Reconciliation transaction failed for %s", code)
    return len(codes)


def build_app() -> procrastinate.App:
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    worker = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    task = worker.task(name="action.reconcile", queue=QUEUE, lock="action.reconcile")(sweep)
    worker.periodic(cron="*/15 * * * *", periodic_id="action.reconcile")(task)
    from app.modules.integration.execution import dispatch_queued

    dispatch = worker.task(name="action.dispatch", queue=QUEUE, lock="action.dispatch")(dispatch_queued)
    worker.periodic(cron="* * * * *", periodic_id="action.dispatch")(dispatch)
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
            await app.run_worker_async(queues=[QUEUE], concurrency=1, shutdown_graceful_timeout=5)
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="仅消费定时对账队列；不重试外部写入")
    parser.add_argument("--install-schema", action="store_true")
    arguments = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(arguments.install_schema))
