"""我的页面定时刷新 + 订阅（问数侧钩子，挂在动态调度 worker 上）。

- `refresh_due_pages(session)`：取到点的页面（`qa_page.next_run <= now`，`FOR UPDATE SKIP LOCKED`，
  多实例不重刷），以**页面所有者**身份（`resolve_user`，现读角色 / 行级范围）走和
  `POST /pages/{pid}:refresh` 同一套 `page_execution.execute_cards`；失败 → 状态「刷新失败」+ 原因，
  不中断其它页面；无论成败都按刷新计划排下次。所有者停用 / 无页面权限 → 刷新失败，不换身份。
- 订阅：问数自己没有投递通道，投递一律写事务性 outbox（`app.common.outbox.emit`，主题
  `qa.page.subscription`，与业务行同事务），由通知侧订阅后发站内信 / 邮件 / 企业微信：
  - `每次刷新后` 且启用 → 刷新成功后出一条（`only_changed` 时结果没变就不出），
    另写审计 `qa.page.subscription.due`；
  - `每天 09:00` / `每周一 09:00`（按 `display_tz`）→ 每个时段出一条摘要（`send_digests`，随每分钟 sweep 跑；
    event_id 带时段键，多实例只出一条；时段后 3 小时内补发，再晚等下个时段）；
    `only_changed` 时和上一条摘要的结果指纹相同就不出。
  - 摘要用页面最近一次刷新的缓存结果（所有者身份算的），收件人只认页面所有者和共享成员（按姓名匹配），
    其它姓名放 `unresolved` 不投——通知侧只能投 `to`。
- 调度：`sweep()` 逐工作空间（`app.jobs.scope`）开带本体范围的会话执行；`register(app)` 把它登记成
  Procrastinate 周期任务 `qa.page.sweep`（每分钟）。由动态调度 worker
  （`app.modules.dynamic.worker.build_app`）调用 `register` 并把 `QUEUE` 加进消费队列——
  这是动态侧要改的两行，问数不改动态代码（见 handoff/qa.md）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.common.audit import audit
from app.common.auth import resolve_user
from app.common.outbox import OutboxEvent, emit
from app.core import clock
from app.core.errors import AppError
from app.modules.qa import page_execution as execution
from app.modules.qa.models import (
    PageStatus,
    QaPage,
    QaPageShareMember,
    QaPageSubscription,
    SubChannel,
    SubWhen,
)

log = logging.getLogger(__name__)

TASK = "qa.page.sweep"
QUEUE = "qa_pages"
LOCK = "qa.page.sweep"
BATCH = 20  # 每个工作空间每轮最多刷几页；剩下的下一分钟接着刷
TOPIC = "qa.page.subscription"  # outbox 主题（通知侧订阅）
DIGEST_HOUR = 9
TOP_ROWS = 3  # 摘要里每张卡片最多列几行
DIGEST_GRACE = timedelta(hours=3)  # 时段过了多久还补发（worker 短暂停机）；再晚就等下个时段，不补旧摘要


def _card_values(page: QaPage) -> list[Any]:
    return [c.result_cache for c in page.cards]


async def emit_refresh_subscription(session, page, actor, at, *, before, ok):
    """Manual and scheduled refresh share the same successful inbox subscription event."""
    sub = page.subscription
    if (not ok or sub is None or not sub.enabled or sub.channel != SubChannel.inbox
            or sub.send_when != SubWhen.after_refresh):
        return None
    changed = _card_values(page) != before
    if sub.only_changed and not changed:
        return None
    delivery = {"channel": sub.channel.value, "recipients": list(sub.recipients or []), "changed": changed}
    await audit(session, actor, action="qa.page.subscription.due", target=page.code, detail=delivery)
    await _emit_once(
        session, f"qa.page.refresh:{page.code}:{at.isoformat()}",
        await _payload(session, page, sub, "afterRefresh", at.isoformat(), changed),
    )
    return delivery


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
    delivery = await emit_refresh_subscription(session, page, actor, at, before=before, ok=ok)
    if delivery is not None:
        out["delivery"] = delivery
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


# ---------------------------------------------------------------- 订阅投递 / 摘要


def _fingerprint(page: QaPage) -> str:
    blob = json.dumps(_card_values(page), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _card_lines(cache: dict[str, Any] | None) -> list[str]:
    """一张卡片的摘要行：单值卡 → 值 + 单位 + 说明；表格卡 → 前几行预览 + 共几行。"""
    c = cache or {}
    if c.get("value") is not None:
        return [f"{c['value']}{c.get('unit') or ''}" + (f"（{c['sub']}）" if c.get("sub") else "")]
    rows = c.get("rows") or []
    out = [f"{r[0]}：{r[1]}" if len(r) > 1 and r[1] else str(r[0]) for r in rows[:TOP_ROWS] if r]
    total = c.get("total")
    if isinstance(total, int) and total > len(out):
        out.append(f"共 {total} 行" + ("以上" if c.get("truncated") else ""))
    return out or ["（暂无结果）"]


def digest_text(page: QaPage) -> str:
    head = f"【{page.name}】"
    if page.data_as_of:
        head += f"数据截至 {page.data_as_of.astimezone(clock.local_now().tzinfo):%Y-%m-%d %H:%M}"
    if page.status != PageStatus.ok:
        head += f"（{page.status.value}{'：' + page.status_note if page.status_note else ''}）"
    lines = [head]
    for card in page.cards:
        body = _card_lines(card.result_cache)
        lines.append(f"· {card.title}：{body[0]}")
        lines += [f"  {x}" for x in body[1:]]
    return "\n".join(lines)


async def _recipients(session, page: QaPage, sub: QaPageSubscription) -> tuple[list[str], list[str]]:
    """收件人姓名 → 用户编号，只认页面所有者和共享成员（摘要是所有者权限算的，不能发给无关的人）。"""
    known: dict[str, list[str]] = {}
    members = await session.scalars(select(QaPageShareMember).where(QaPageShareMember.page_id == page.id))
    people = [page.owner] + [m.user for m in members]
    for u in people:
        if u is not None:
            known.setdefault(u.name, [])
            if u.code not in known[u.name]:
                known[u.name].append(u.code)
    to, unresolved = [], []
    for name in sub.recipients or []:
        codes = known.get(name) or []
        if len(codes) == 1:
            to.append(codes[0])
        else:
            unresolved.append(name)  # 不认识或重名：不猜
    if not sub.recipients and page.owner is not None:
        to.append(page.owner.code)  # 没填收件人 = 发给自己
    return list(dict.fromkeys(to)), unresolved


async def _payload(
    session, page: QaPage, sub: QaPageSubscription, kind: str, slot: str, changed: bool
) -> dict[str, Any]:
    to, unresolved = await _recipients(session, page, sub)
    return {
        "ontologyId": page.ontology_id,
        "kind": kind,
        "slot": slot,
        "pageId": page.code,
        "pageName": page.name,
        "channel": sub.channel.name,
        "channelLabel": sub.channel.value,
        "to": to,
        "unresolved": unresolved,
        "changed": changed,
        "status": page.status.value,
        "dataAsOf": page.data_as_of.isoformat() if page.data_as_of else None,
        "fingerprint": _fingerprint(page),
        "title": f"页面订阅：{page.name}",
        "text": digest_text(page),
        "link": f"/qa/pages/{page.code}",
    }


async def _sent(session, event_id: str) -> bool:
    return bool(await session.scalar(select(OutboxEvent.id).where(OutboxEvent.event_id == event_id)))


async def _emit_once(session, event_id: str, payload: dict[str, Any]) -> None:
    if not await _sent(session, event_id):
        await emit(session, TOPIC, payload, event_id=event_id)


def digest_slot(when: SubWhen, at: datetime) -> tuple[str, datetime] | None:
    """到了哪个摘要时段（本地时区）：每天 09:00 / 每周一 09:00；还没到第一个时段 → None。"""
    local = at.astimezone(clock.local_now().tzinfo)
    today = local.replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0)
    if when == SubWhen.daily_0900:
        slot = today if local >= today else today - timedelta(days=1)
        return f"D{slot:%Y%m%d}", slot
    if when == SubWhen.weekly_mon_0900:
        slot = today - timedelta(days=local.weekday())
        if local < slot:
            slot -= timedelta(days=7)
        return f"W{slot:%Y%m%d}", slot
    return None


async def send_digests(session, *, at: datetime | None = None) -> list[dict[str, Any]]:
    """当前工作空间里到点的每日 / 每周摘要各出一条 outbox 事件（同一时段只出一次）。"""
    at = at or clock.now()
    subs = list(
        await session.scalars(
            select(QaPageSubscription).where(
                QaPageSubscription.enabled.is_(True),
                QaPageSubscription.send_when.in_([SubWhen.daily_0900, SubWhen.weekly_mon_0900]),
            )
        )
    )
    out = []
    for sub in subs:
        hit = digest_slot(sub.send_when, at)
        if hit is None:
            continue
        key, slot = hit
        if at - slot > DIGEST_GRACE:
            continue
        page = await session.scalar(
            select(QaPage).where(QaPage.id == sub.page_id).options(selectinload(QaPage.cards))
        )
        if page is None:
            continue
        event_id = f"qa.page.digest:{page.code}:{key}"
        if await _sent(session, event_id):
            continue
        last = await session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.topic == TOPIC, OutboxEvent.event_id.like(f"qa.page.digest:{page.code}:%"))
            .order_by(OutboxEvent.id.desc())
            .limit(1)
        )
        fp = _fingerprint(page)
        changed = last is None or (last.payload or {}).get("fingerprint") != fp
        if sub.only_changed and not changed:
            continue
        kind = "daily" if sub.send_when == SubWhen.daily_0900 else "weekly"
        payload = await _payload(session, page, sub, kind, key, changed)
        await emit(session, TOPIC, payload, event_id=event_id)
        out.append({"page": page.code, "slot": key, "channel": payload["channel"], "to": payload["to"]})
    return out


async def sweep(at: datetime | None = None) -> dict[int, list[dict[str, Any]]]:
    """逐工作空间刷新到点页面；一个空间出错不影响别的空间。"""
    from app.jobs.scope import scoped_sessions, workspace_ids

    done: dict[int, list[dict[str, Any]]] = {}
    for oid in await workspace_ids():
        try:
            async with scoped_sessions(oid)() as session, session.begin():
                done[oid] = await refresh_due_pages(session, at=at)
                done[oid] += [{"digest": d} for d in await send_digests(session, at=at)]
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
