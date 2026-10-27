"""我的页面定时刷新 + 订阅（问数侧钩子，挂在动态调度 worker 上）。

- `refresh_due_pages(session)`：取到点的页面（`qa_page.next_run <= now`，`FOR UPDATE SKIP LOCKED`，
  多实例不重刷），以**页面所有者**身份（`resolve_user`，现读角色 / 行级范围）走和
  `POST /pages/{pid}:refresh` 同一套 `page_execution.execute_cards`；失败 → 状态「刷新失败」+ 原因，
  不中断其它页面；无论成败都按刷新计划排下次。所有者停用 / 无页面权限 → 刷新失败，不换身份。
- 订阅：`send_when = 每次刷新后` 且启用 → 刷新成功后出一条投递记录（`only_changed` 时结果没变就不出）。
  问数自己没有投递通道：记录写审计 `qa.page.subscription.due`（目标页、渠道、收件人、是否变化），
  由通知侧消费。「每天 / 每周一 09:00」摘要投递见 handoff 未完成项。
- 调度：`sweep()` 逐工作空间（`app.jobs.scope`）开带本体范围的会话执行；`register(app)` 把它登记成
  Procrastinate 周期任务 `qa.page.sweep`（每分钟）。由动态调度 worker
  （`app.modules.dynamic.worker.build_app`）调用 `register` 并把 `QUEUE` 加进消费队列——
  这是动态侧要改的两行，问数不改动态代码（见 handoff/qa.md）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.common.audit import audit
from app.common.auth import resolve_user
from app.core import clock
from app.core.errors import AppError
from app.modules.qa import page_execution as execution
from app.modules.qa.models import PageStatus, QaPage, SubWhen

log = logging.getLogger(__name__)

TASK = "qa.page.sweep"
QUEUE = "qa_pages"
LOCK = "qa.page.sweep"
BATCH = 20  # 每个工作空间每轮最多刷几页；剩下的下一分钟接着刷


def _card_values(page: QaPage) -> list[Any]:
    return [c.result_cache for c in page.cards]


async def refresh_page_once(session, page: QaPage, at: datetime) -> dict[str, Any]:
    """刷新一页（同 P6 立即刷新），排下次，返回 {page, status, delivery?}。"""
    from app.modules.qa.pages_api import _status_of, next_run

    before = _card_values(page)
    actor = None
    try:
        async with session.begin_nested():  # 单页失败只回滚这一页的卡片缓存
            from app.modules.admin.models import SysUser

            owner = await session.scalar(select(SysUser.code).where(SysUser.id == page.owner_id))
            if owner is None:
                raise AppError("页面所有者不存在", code="QA_PAGE_OWNER_MISSING", status_code=409)
            actor = await resolve_user(session, owner)
            viewer = await execution.current_viewer(session, actor)
            permission = await execution.fingerprint(viewer)
            caches, as_of = await execution.execute_cards(session, viewer, page.cards)
            for card, cache in zip(page.cards, caches, strict=True):
                card.result_cache, card.perm_fingerprint = cache, permission
    except AppError as error:
        page.status, page.status_note = PageStatus.refresh_failed, error.message
        ok = False
    else:
        page.status, page.status_note = _status_of(page.cards)
        page.last_refresh, page.data_as_of = at, as_of
        ok = True
    page.next_run = next_run(page.refresh, at)
    out: dict[str, Any] = {"page": page.code, "status": page.status.value, "ok": ok}
    sub = page.subscription
    if ok and sub is not None and sub.enabled and sub.send_when == SubWhen.after_refresh:
        changed = _card_values(page) != before
        if changed or not sub.only_changed:
            out["delivery"] = {
                "channel": sub.channel.value,
                "recipients": list(sub.recipients or []),
                "changed": changed,
            }
            await audit(
                session, actor, action="qa.page.subscription.due", target=page.code, detail=out["delivery"]
            )
    await audit(
        session,
        actor,
        action="qa.page.refresh",
        target=page.code,
        result="成功" if ok else "失败",
        detail={"status": page.status.value, "cards": len(page.cards), "scheduled": True},
    )
    return out


async def refresh_due_pages(
    session, *, at: datetime | None = None, limit: int = BATCH
) -> list[dict[str, Any]]:
    at = at or clock.now()
    pages = list(
        await session.scalars(
            select(QaPage)
            .where(QaPage.next_run.is_not(None), QaPage.next_run <= at)
            .options(selectinload(QaPage.cards), selectinload(QaPage.subscription))
            .order_by(QaPage.next_run, QaPage.id)
            .limit(limit)
            .with_for_update(skip_locked=True, of=QaPage)
        )
    )
    out = []
    for page in pages:
        try:
            out.append(await refresh_page_once(session, page, at))
        except Exception:  # 非业务异常也不拖垮整轮；排到下次，避免每分钟重试同一坏页
            log.exception("qa page scheduled refresh failed: %s", page.code)
            from app.modules.qa.pages_api import next_run

            page.status, page.status_note = PageStatus.refresh_failed, "定时刷新异常"
            page.next_run = next_run(page.refresh, at)
            out.append({"page": page.code, "status": page.status.value, "ok": False})
    await session.flush()
    return out


async def sweep(at: datetime | None = None) -> dict[int, list[dict[str, Any]]]:
    """逐工作空间刷新到点页面；一个空间出错不影响别的空间。"""
    from app.jobs.scope import scoped_sessions, workspace_ids

    done: dict[int, list[dict[str, Any]]] = {}
    for oid in await workspace_ids():
        try:
            async with scoped_sessions(oid)() as session, session.begin():
                done[oid] = await refresh_due_pages(session, at=at)
        except Exception:
            log.exception("qa page sweep failed for workspace %s", oid)
    return done


async def _task(timestamp: int = 0) -> None:
    """Procrastinate 周期任务会传 `timestamp`（计划触发时刻）；到点判断用当前时间，不用它。"""
    await sweep()


def register(app) -> None:
    """给动态调度 worker 用（约定：`build_app()` 里调 `page_schedule.register(app)`，
    `run_worker_async(queues=[..., page_schedule.QUEUE])`）：登记每分钟一次的 `qa.page.sweep`
    （队列 `qa_pages`，全局锁防并发）。"""
    task = app.task(name=TASK, queue=QUEUE, lock=LOCK)(_task)
    app.periodic(cron="* * * * *", periodic_id=TASK)(task)
