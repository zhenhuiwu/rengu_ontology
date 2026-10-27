"""每日自动评测运行（不只是出题）：逐工作空间建一条评测运行并在同一事务里入 `qa-eval` 队列。

- 周期任务 `qa.eval.daily`（CRON 03:00，晚于 02:30 的自动出题），登记在评测专用 worker
  （`app.modules.qa.eval.worker.build_app`）上，同队列、独立锁；运行本身仍由 `qa.eval.run` 执行，
  结果照常写 `qa_eval_run` / `qa_eval_fail` / 每题 last_result。
- 发起身份 = 本空间最近一次评测运行的发起人（`eval_gen.eval_user`，须仍有 qa-tuning 管理权）；
  没有就跳过（不替人造身份）。本空间已有排队 / 运行中的评测、或今天已跑过每日评测 → 跳过。
- 题集 = 全部 semplan 题（含自动题，按本体过滤）；不带会话口径 / 范围。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import require_ontology_id
from app.core import clock
from app.modules.qa.models import QaEvalRun, RunStatus

log = logging.getLogger(__name__)

TASK = "qa.eval.daily"
LOCK = "qa.eval.daily"
CRON = "0 3 * * *"
TRIGGER = "每日自动"


def _day_start(at: datetime) -> datetime:
    local = at.astimezone(clock.local_now().tzinfo)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def schedule(
    session: AsyncSession, user: Any = None, *, at: datetime | None = None, defer=None
) -> str | None:
    """给当前工作空间建一条每日评测运行并入队；返回运行编号，跳过时返回 None 并记日志原因。"""
    from app.modules.qa import eval_gen
    from app.modules.qa.eval import job
    from app.modules.qa.router import next_run_code
    from app.modules.versioning.service import VersioningService

    at = at or clock.now()
    oid = require_ontology_id(session)
    user = user or await eval_gen.eval_user(session)
    if user is None:
        log.info("qa daily eval skipped for workspace %s: no eval operator", oid)
        return None
    busy = await session.scalar(
        select(QaEvalRun.code)
        .where(
            (QaEvalRun.status == RunStatus.running)
            | ((QaEvalRun.status == RunStatus.queued) & QaEvalRun.queue_job_id.is_not(None))
        )
        .limit(1)
    )
    if busy:
        log.info("qa daily eval skipped for workspace %s: %s is active", oid, busy)
        return None
    done_today = await session.scalar(
        select(QaEvalRun.code)
        .where(QaEvalRun.trigger_label == TRIGGER, QaEvalRun.run_at >= _day_start(at))
        .limit(1)
    )
    if done_today:
        return None
    qs = [q for q in (await session.scalars(job.questions_query(None))).all() if job.own_question(q, oid)]
    if not qs:
        return None
    n = len(qs)
    run = QaEvalRun(
        code=await next_run_code(session),
        run_at=at,
        trigger_label=TRIGGER,
        version_label=await VersioningService().current_version(session),
        scope=f"全部 {n} 题",
        total=n,
        passed=0,
        status=RunStatus.queued,
        operator="系统",
        execution_context={
            "ontologyId": oid,
            "userId": user.id,
            "tenantId": user.tenant_id,
            "sessionId": None,
            "group": None,
            "scope": None,
            "caliber": None,
            "trigger": "daily",
        },
    )
    session.add(run)
    await session.flush()
    if defer is None:
        from app.modules.qa.eval.queue import defer
    run.queue_job_id = await defer(session, run.code)
    await session.flush()
    return run.code


async def schedule_all(at: datetime | None = None) -> dict[int, str | None]:
    """逐工作空间；一个空间出错不影响别的空间。"""
    from app.jobs.scope import scoped_sessions, workspace_ids

    out: dict[int, str | None] = {}
    for oid in await workspace_ids():
        try:
            async with scoped_sessions(oid)() as session, session.begin():
                out[oid] = await schedule(session, at=at)
        except Exception:
            log.exception("qa daily eval scheduling failed for workspace %s", oid)
    return out


async def _task(timestamp: int = 0) -> None:
    await schedule_all()


def register(app, queue: str) -> None:
    task = app.task(name=TASK, queue=queue, lock=LOCK)(_task)
    app.periodic(cron=CRON, periodic_id=TASK)(task)
