"""Dedicated subscriber queue: python -m app.modules.event.worker."""

import argparse
import asyncio
import logging
import sys
from datetime import timedelta

import procrastinate
from sqlalchemy import case, exists, func, or_, select

from app.core import clock
from app.core.config import get_settings
from app.db import models as _models  # noqa: F401
from app.db.session import dispose_engine
from app.jobs.scope import scoped_sessions, workspace_ids
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.event.delivery import discover, due, execute, prepare, recover
from app.modules.event.models import Event, EventDelivery, EventOutcome, EventType

QUEUE = "event-delivery"
logger = logging.getLogger(__name__)


async def sweep(timestamp: int = 0) -> int:
    total = 0
    for ontology_id in await workspace_ids():
        from app.common.ontology import WorkspaceInactive
        try:
            total += await _sweep_workspace(ontology_id)
        except WorkspaceInactive:
            continue
    return total


async def _sweep_workspace(ontology_id: int) -> int:
    sessions = scoped_sessions(ontology_id)
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


async def deliver_event(ontology_id: int, event_code: str, subscriber: str) -> int:
    """Process only one explicitly selected event/subscriber, without sweeping other work."""
    sessions = scoped_sessions(ontology_id)
    async with sessions() as session, session.begin():
        event = await session.scalar(select(Event).where(Event.code == event_code))
        subscribers = (event.definition_snapshot or {}).get("subscribers") if event else None
        if (
            not isinstance(subscribers, list)
            or len(subscribers) != 1
            or not isinstance(subscribers[0], dict)
            or subscribers[0].get("name") != subscriber
        ):
            raise ValueError("Selected event must have exactly the named subscriber")
        await discover(session, event_code)
        delivery_id = await session.scalar(
            select(EventDelivery.id)
            .join(Event, Event.id == EventDelivery.event_id)
            .where(Event.code == event_code, EventDelivery.subscriber_name == subscriber)
        )
        if delivery_id is not None:
            await recover(session, delivery_id)
    if delivery_id is None:
        return 0
    async with sessions() as session, session.begin():
        claimed = await prepare(session, delivery_id)
    if claimed:
        async with sessions() as session, session.begin():
            await execute(session, delivery_id)
    return int(claimed)


async def run_selected(ontology_id: int, event_type: str, subscriber: str, after_id: int) -> None:
    from app.common.ontology import WorkspaceInactive
    try:
        await _run_selected(ontology_id, event_type, subscriber, after_id)
    except WorkspaceInactive:
        return


async def _run_selected(ontology_id: int, event_type: str, subscriber: str, after_id: int) -> None:
    """Poll only new events of one explicitly configured type after a fixed high-water mark."""
    sessions = scoped_sessions(ontology_id)
    while True:
        async with sessions() as session:
            subscribers = Event.definition_snapshot["subscribers"]
            target = select(EventDelivery.id).where(
                EventDelivery.event_id == Event.id, EventDelivery.subscriber_name == subscriber
            )
            due_target = target.where(
                or_(
                    (EventDelivery.state == "pending")
                    & or_(EventDelivery.next_at.is_(None), EventDelivery.next_at <= clock.now()),
                    (EventDelivery.state == "processing")
                    & (EventDelivery.started_at < clock.now() - timedelta(minutes=5)),
                ),
            )
            codes = list(
                await session.scalars(
                    select(Event.code)
                    .join(EventType, Event.event_type_id == EventType.id)
                    .where(
                        Event.id > after_id,
                        EventType.code == event_type,
                        Event.is_seed.is_(False),
                        Event.is_test.is_(False),
                        Event.outcome.in_((EventOutcome.pending, EventOutcome.partial)),
                        Event.definition_snapshot.is_not(None),
                        case(
                            (func.jsonb_typeof(subscribers) == "array", func.jsonb_array_length(subscribers)),
                            else_=0,
                        ) == 1,
                        subscribers[0]["name"].as_string() == subscriber,
                        or_(~exists(target), exists(due_target)),
                    )
                    .order_by(Event.id)
                    .limit(200)
                )
            )
        for code in codes:
            try:
                await deliver_event(ontology_id, code, subscriber)
            except Exception:
                logger.error("Selected event delivery failed for %s", code)
        await asyncio.sleep(10)


def build_app() -> procrastinate.App:
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    worker = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    task = worker.task(name="event.deliver", queue=QUEUE, lock="event.deliver")(sweep)
    worker.periodic(cron="* * * * *", periodic_id="event.deliver")(task)
    from app.jobs import dispatch_outbox

    inbox = worker.task(name="outbox.dispatch", queue=QUEUE, lock="outbox.dispatch")(dispatch_outbox)
    worker.periodic(cron="* * * * *", periodic_id="outbox.dispatch")(inbox)
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
    parser.add_argument("--ontology", type=int)
    parser.add_argument("--event-type")
    parser.add_argument("--subscriber")
    parser.add_argument("--after-id", type=int)
    arguments = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    selected = (arguments.ontology, arguments.event_type, arguments.subscriber, arguments.after_id)
    if any(value is not None for value in selected):
        if (
            arguments.install_schema
            or not arguments.ontology
            or not arguments.event_type
            or not arguments.subscriber
            or arguments.after_id is None
            or arguments.after_id < 0
        ):
            parser.error("Selected mode requires --ontology, --event-type, --subscriber and --after-id")
        asyncio.run(run_selected(*selected))
    else:
        asyncio.run(main(arguments.install_schema))
