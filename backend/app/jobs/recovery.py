"""Retire only orphaned periodic queue shells; never retry a business operation."""

import asyncio
from contextlib import suppress
from datetime import timedelta
import logging
import zlib

import procrastinate

log = logging.getLogger(__name__)
STALE_SECONDS = 120
PERIODIC = frozenset({
    ("events", "event.redeliver_failed"), ("outbox", "outbox.dispatch"),
    ("maintenance", "admin.expire_api_keys"),
    ("event-delivery", "event.deliver"), ("event-delivery", "outbox.dispatch"),
    ("reconcile", "action.reconcile"), ("reconcile", "action.dispatch"),
    ("rules", "rule.sweep"), ("qa_pages", "qa.page.sweep"),
    ("quality", "quality.sweep"),
    ("qa-eval", "qa.eval.generate"), ("qa-eval", "qa.eval.daily"),
})


def ownership_key(job_id):
    return zlib.crc32(f"procrastinate.periodic.{job_id}".encode())


def recoverable(row):
    args = row["args"]
    if ((row["queue_name"], row["task_name"]) not in PERIODIC or row["status"] != "doing"
            or not isinstance(args, dict) or set(args) - {"timestamp"}
            or "timestamp" in args and type(args["timestamp"]) is not int):
        return False
    observed = row.get("last_heartbeat") or row.get("started_at")
    return observed is not None and row["checked_at"] - observed > timedelta(seconds=STALE_SECONDS)


async def periodic_ownership(call_next, context, worker):
    job = context.job
    if (job.queue, job.task_name) not in PERIODIC:
        return await call_next()
    connector = context.app.connector
    async with connector.pool.connection() as connection:
        key = ownership_key(job.id)
        try:
            await connection.execute("SELECT pg_advisory_lock(%s)", (key,))
            await connection.commit()
            rows = await connector.execute_query_all_async_with_connection(
                connection, query="""SELECT id FROM procrastinate_jobs
                WHERE id=%(id)s AND status='doing' AND worker_id IS NOT DISTINCT FROM %(owner)s""",
                id=job.id, owner=job.worker_id,
            )
            if not rows:
                raise RuntimeError("Periodic queue ownership changed before execution")
            await connection.commit()
            return await call_next()
        finally:
            try:
                await connection.rollback()
                await connection.execute("SELECT pg_advisory_unlock(%s)", (key,))
                await connection.commit()
            except BaseException:
                await connection.close()  # A connection with an uncertain session lock cannot reenter the pool.
                raise


async def recover_periodic(app, queues=None):
    active = {(entry.task.queue, entry.task.name) for entry in app.periodic_registry.periodic_tasks.values()}
    allowed = PERIODIC & active
    if queues is not None:
        allowed = {item for item in allowed if item[0] in queues}
    count = 0
    for queue, task in sorted(allowed):
        candidates = await app.job_manager.get_stalled_jobs(
            queue=queue, task_name=task, seconds_since_heartbeat=STALE_SECONDS,
        )
        for candidate in list(candidates)[:100]:
            connector = app.connector
            async with connector.pool.connection() as connection, connection.transaction():
                rows = await connector.execute_query_all_async_with_connection(
                    connection, query="""SELECT j.id, j.queue_name, j.task_name, j.args, j.status, j.worker_id,
                    clock_timestamp() AS checked_at,
                    (SELECT max(at) FROM procrastinate_events WHERE job_id=j.id AND type='started') AS started_at
                    FROM procrastinate_jobs j WHERE j.id=%(id)s FOR UPDATE OF j SKIP LOCKED""",
                    id=candidate.id,
                )
                if not rows:
                    continue
                row = rows[0]
                if (row["queue_name"], row["task_name"]) not in allowed:
                    continue
                owners = await connector.execute_query_all_async_with_connection(
                    connection, query="""SELECT last_heartbeat FROM procrastinate_workers
                    WHERE id=%(owner)s FOR UPDATE""", owner=row["worker_id"],
                )
                row["last_heartbeat"] = owners[0]["last_heartbeat"] if owners else None
                if not recoverable(row):
                    continue
                held = await connector.execute_query_one_async_with_connection(
                    connection, query="SELECT pg_try_advisory_xact_lock(%(key)s) AS acquired",
                    key=ownership_key(row["id"]),
                )
                if not held["acquired"]:
                    continue
                await connector.execute_query_one_async_with_connection(
                    connection, query="SELECT procrastinate_finish_job_v1(%(id)s, 'failed', false)", id=row["id"],
                )
            count += 1
            log.warning("Retired orphan periodic shell: queue=%s task=%s job=%s; business work was not replayed",
                        queue, task, candidate.id)
    return count


async def _recovery_loop(app, queues):
    while True:
        await asyncio.sleep(30)
        try:
            await recover_periodic(app, queues)
        except Exception as error:
            log.error("Periodic recovery unavailable: %s", type(error).__name__)


class ReliableApp(procrastinate.App):
    """Use the public worker lifecycle and middleware hooks for every existing CLI."""

    def __init__(self, **kwargs):
        defaults = dict(kwargs.pop("worker_defaults", None) or {})
        defaults["worker_middleware"] = [periodic_ownership, *defaults.get("worker_middleware", [])]
        super().__init__(worker_defaults=defaults, **kwargs)

    async def run_worker_async(self, **kwargs):
        queues = kwargs.get("queues", self.worker_defaults.get("queues"))
        await recover_periodic(self, queues)
        recovery = asyncio.create_task(_recovery_loop(self, queues))
        try:
            await super().run_worker_async(**kwargs)
        finally:
            recovery.cancel()
            with suppress(asyncio.CancelledError):
                await recovery
