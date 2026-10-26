"""Durable reconciliation, with execution-row locks and atomic dynamic callbacks."""

import logging
import time
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.contracts.actions import ReconcileOutcome, ReconcileResult
from app.core import clock
from app.core.errors import AppError, Conflict, NotFound
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.integration.adapters import get_adapter, unavailable
from app.modules.integration.models import (
    CallKind,
    CallResult,
    ExtSystem,
    ReconcileState,
    ReconcileTask,
    WritebackCall,
)

INTERVAL = timedelta(minutes=15)
MAX_TRIES = 3
logger = logging.getLogger(__name__)


async def lock_run(session, code: str) -> ActionRun:
    run = await session.scalar(
        select(ActionRun)
        .where(ActionRun.code == code)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise NotFound("动作不存在")
    return run


class DatabaseReconcileScheduler:
    def __init__(self, session):
        self.session = session

    async def ensure(self, run: ActionRun) -> ReconcileTask:
        """Caller must hold the execution row lock; creation is idempotent in that transaction."""
        task = await self.session.scalar(
            select(ReconcileTask)
            .where(ReconcileTask.action_run_id == run.id)
            .execution_options(populate_existing=True)
        )
        if task is not None:
            return task
        system = await self.session.scalar(select(ExtSystem).where(ExtSystem.code == run.sys_code))
        if system is None:
            raise unavailable("动作没有可用的外部系统，不能建立对账任务")
        manual = run.is_seed or not system.idempotent
        task = ReconcileTask(
            action_run_id=run.id,
            sys_id=system.id,
            idem_key=run.idem_key,
            state=ReconcileState.manual if manual else ReconcileState.querying,
            round=1,
            tries=3 if run.is_seed else 0,
            next_at=None if manual else clock.now() + INTERVAL,
            manual=manual,
            is_seed=run.is_seed,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def reopen_reconcile(self, action_run_id: str) -> None:
        run = await lock_run(self.session, action_run_id)
        if run.status != ActionStatus.unknown:
            raise Conflict("只有结果未知的动作可以重新开启对账", code="STATE_INVALID")
        task = await self.ensure(run)
        system = await self.session.get(ExtSystem, task.sys_id)
        if system.idempotent:
            get_adapter(system)  # Missing configuration is a real 503; no round or counter changes.
        task.round += 1
        task.tries = 0
        task.manual = not system.idempotent
        task.state = ReconcileState.manual if task.manual else ReconcileState.querying
        task.next_at = None if task.manual else clock.now()
        task.last_error = None
        await self.session.flush()

    async def resolve_reconcile(self, action_run_id: str, outcome: ReconcileOutcome, reconciler: str) -> None:
        run = await lock_run(self.session, action_run_id)
        if (
            run.status != ActionStatus.reconciled
            or run.reconcile_result is None
            or run.reconcile_result.name != outcome.value
        ):
            raise Conflict("动作尚未完成该对账结论", code="STATE_INVALID")
        task = await self.ensure(run)
        resolved(task, outcome.value, reconciler)
        await self.session.flush()


def to_manual(task: ReconcileTask, error: str | None = None) -> None:
    task.state, task.manual, task.next_at = ReconcileState.manual, True, None
    task.last_error = error


def resolved(task: ReconcileTask, result: str, reconciler: str | None) -> None:
    task.state, task.manual, task.next_at = ReconcileState.resolved, False, None
    task.reconcile_result, task.reconciler = result, reconciler
    task.resolved_at, task.last_error = clock.now(), None


async def process(session, code: str, listener, adapter_factory=get_adapter) -> ReconcileTask:
    """The HTTP GET and callback are serialized per run; callback failures retain an honest manual task."""
    run = await lock_run(session, code)
    task = await DatabaseReconcileScheduler(session).ensure(run)
    if task.state == ReconcileState.resolved:
        return task
    if run.status == ActionStatus.reconciled:
        if run.reconcile_result is None:
            to_manual(task, "ACTION_RECONCILE_RESULT_MISSING")
            return task
        resolved(task, run.reconcile_result.name, run.reconcile_by)
        return task
    if (
        run.status != ActionStatus.unknown
        or task.state != ReconcileState.querying
        or task.next_at is None
        or task.next_at > clock.now()
    ):
        return task
    system = await session.get(ExtSystem, task.sys_id)
    from app.modules.integration.execution import queued_call

    if await queued_call(session, run.id):
        return task  # A queued write has not reached the external system yet.
    if not system.idempotent:
        to_manual(task, "IDEMPOTENCY_QUERY_UNSUPPORTED")
        return task
    try:
        adapter = adapter_factory(system)
    except AppError as exc:
        to_manual(task, exc.code)
        return task
    started = time.perf_counter()
    result, error = None, None
    try:
        result = await adapter.query_by_key(task.idem_key)
    except AppError as exc:
        error = exc.code
    task.tries += 1  # Only a real query attempt consumes one of the three tries.
    task.last_ext_status = result.ext_status.value if result else None
    task.last_error = error
    session.add(
        WritebackCall(
            code=f"REC-{uuid.uuid4().hex}",
            sys_id=system.id,
            action_run_id=run.id,
            kind=CallKind.query,
            at=clock.now(),
            op_name="幂等查询",
            target_label=run.obj_label or run.code,
            method="GET",
            path=adapter.path("query"),
            idem_key=task.idem_key,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            http_status=getattr(adapter, "last_http", None),
            result=CallResult.failed if error else CallResult.succeeded,
            attempt=task.tries,
            actor="系统",
            msg=error or result.ext_status.value,
        )
    )
    if result and result.ext_status.outcome is not None:
        try:
            async with session.begin_nested():
                await listener.on_reconciled(
                    run.code, ReconcileResult(result.ext_status.outcome, result.ext_ref)
                )
        except AppError as exc:
            to_manual(task, f"CALLBACK_{exc.code}")
        else:
            resolved(task, result.ext_status.outcome.value, "系统")
    elif task.tries >= MAX_TRIES:
        to_manual(task, error)
    else:
        task.next_at = clock.now() + INTERVAL
    await session.flush()
    return task


async def discover(session) -> None:
    """Recover unknown executions after restarts; seed history stays manual and never gets queried."""
    rows = await session.scalars(
        select(ActionRun)
        .outerjoin(ReconcileTask, ReconcileTask.action_run_id == ActionRun.id)
        .where(
            ActionRun.status == ActionStatus.unknown,
            ReconcileTask.id.is_(None),
            ActionRun.sys_code.in_(select(ExtSystem.code)),
        )
        .order_by(ActionRun.id)
        .with_for_update(of=ActionRun, skip_locked=True)
        .limit(200)
    )
    scheduler = DatabaseReconcileScheduler(session)
    for run in rows:
        try:
            await scheduler.ensure(run)
        except AppError as exc:
            # shortcut: missing system prevents a task FK; retry after system configuration.
            logger.warning("Cannot create reconciliation task for %s: %s", run.code, exc.code)
            continue
