"""事件类型读取（同 action.service：当前读展开表，`?ver=` 读快照 eventTypes 段）。

EventRuntimeService（palantir-r2）：近期事件 / 运行健康 / 近 14 天趋势，读 event、event_daily_stat。
运行数据不分版本：`?ver=` 不影响，按当前 event_type 表找类型（不存在 404）[推断]。

weekly（列表 / 详情 / 订阅方的近 7 天次数）是运行统计，不分版本，始终取当前表里的值
（seed 来自 mock；以后由事件统计任务刷新）[推断]。
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.common.context import ViewContext
from app.common.pagination import Page, PageParams
from app.core import clock
from app.core.errors import AppError, NotFound
from app.modules.action.service import ALL, defs_for_view, find
from app.modules.event.models import (
    Event,
    EventEnvelopeField,
    EventOutcome,
    EventPayloadField,
    EventSource,
    EventType,
)
from app.modules.event.repo import EventRepo, EventTypeRepo
from app.modules.event.sections import SECTION, event_def, field_dict


class EventTypeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = EventTypeRepo(session)
        self._rows: list[EventType] | None = None

    async def rows(self) -> list[EventType]:
        if self._rows is None:
            self._rows = list(await self.repo.all_ordered())
        return self._rows

    async def defs(self, ctx: ViewContext) -> list[dict[str, Any]]:
        rows = (
            (
                await self.session.execute(
                    select(EventType.code, EventType.retired_release_id).order_by(EventType.id)
                )
            ).all()
            if ctx.ver
            else await self.rows()
        )
        return await defs_for_view(self.session, ctx, rows, SECTION, event_def)

    async def one(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        if ctx.ver:
            return find(await self.defs(ctx), type_id, "事件类型不存在")
        row = await self.repo.get_by_code_or_404(type_id)
        if row.retired_release_id is not None:
            raise NotFound("事件类型不存在", details={"id": type_id})
        return event_def(row)

    async def search_page(self, ctx: ViewContext, page: PageParams, source: str | None, kw: str | None):
        if ctx.ver:
            rows = await self.search(ctx, source, kw)
            p = page.clamp(len(rows))
            return Page.of(rows[p.offset : p.offset + p.size], len(rows), page)
        if kw and ("\x00" in kw or len(kw) > 200):
            raise AppError("搜索文字不能超过200字或包含空字符", code="VALIDATION_FAILED", status_code=422)
        statement = select(EventType).options(raiseload("*")).where(EventType.retired_release_id.is_(None))
        if source and source != ALL:
            try:
                value = EventSource(source)
            except ValueError:
                return Page.of([], 0, page)
            statement = statement.where(EventType.source == value)
        if kw:
            statement = statement.where(EventType.name.contains(kw, autoescape=True))
        rows, total = await self.repo.list_page(statement.order_by(EventType.id), page)
        return Page.of(
            [
                {
                    "id": r.code,
                    "name": r.name,
                    "api": r.api,
                    "source": r.source.value,
                    "status": r.status.value,
                    "version": r.version,
                    "object": r.object,
                }
                for r in rows
            ],
            total,
            page,
        )

    @staticmethod
    def sources() -> list[str]:
        return [ALL, *(s.value for s in EventSource)]

    async def search(self, ctx: ViewContext, source: str | None, kw: str | None) -> list[dict[str, Any]]:
        return [
            d
            for d in await self.defs(ctx)
            if (not source or source == ALL or d["source"] == source) and (not kw or kw in d["name"])
        ]

    async def detail(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        d = await self.one(ctx, type_id)
        weekly = await self.session.scalar(select(EventType.weekly).where(EventType.code == type_id))
        return {**d, "weekly": weekly or 0, "specific": d.get("payload") or []}

    async def payload_fields(self, ctx: ViewContext, type_id: str) -> tuple[list[dict[str, Any]], int]:
        """payloadOf()：专有字段 + 通用信封字段（envelope=true）。"""
        d = await self.one(ctx, type_id)
        env = [{**field_dict(f), "envelope": True} for f in await self.repo.envelope()]
        specific = [{**f, "envelope": False} for f in d.get("payload") or []]
        return specific + env, len(env)

    async def payload_page(self, ctx: ViewContext, type_id: str, page: PageParams):
        if ctx.ver:
            rows, envelope_count = await self.payload_fields(ctx, type_id)
            p = page.clamp(len(rows))
            return Page.of(rows[p.offset : p.offset + p.size], len(rows), page), envelope_count
        parent = await self.session.scalar(
            select(EventType.id).where(EventType.code == type_id, EventType.retired_release_id.is_(None))
        )
        if parent is None:
            raise NotFound("事件类型不存在", details={"id": type_id})

        def fields(model, envelope):
            return select(
                model.name,
                model.api,
                model.type,
                model.required,
                model.description.label("desc"),
                model.sample,
                (literal(None, type_=EventPayloadField.schema.type) if envelope else EventPayloadField.schema).label("schema"),
                literal(envelope).label("envelope"),
                literal(int(envelope)).label("part"),
                model.seq,
                model.id,
            )

        specific = fields(EventPayloadField, False).where(EventPayloadField.event_type_id == parent)
        merged = union_all(specific, fields(EventEnvelopeField, True)).subquery()
        total = int(await self.session.scalar(select(func.count()).select_from(merged)) or 0)
        envelope_count = int(
            await self.session.scalar(select(func.count()).select_from(EventEnvelopeField)) or 0
        )
        p = page.clamp(total)
        rows = await self.session.execute(
            select(merged).order_by(merged.c.part, merged.c.seq, merged.c.id).limit(p.size).offset(p.offset)
        )
        return Page.of([dict(r._mapping) for r in rows], total, page), envelope_count

    async def subscribers(self, ctx: ViewContext, type_id: str) -> list[dict[str, Any]]:
        d = await self.one(ctx, type_id)
        cur = await self.repo.get_by_code(type_id)
        weekly = {s.name: s.weekly for s in cur.subscribers} if cur else {}
        return [{**s, "weekly": weekly.get(s["name"], 0)} for s in d.get("subscribers") or []]

    async def versions(self, ctx: ViewContext, type_id: str) -> list[dict[str, Any]]:
        """和详情一样按视图判断可见：已下线（或 ?ver= 那一版里没有）的类型 404。"""
        await self.one(ctx, type_id)
        et: EventType = await self.repo.get_by_code_or_404(type_id)
        return [
            {
                "ver": v.ver,
                "release": v.release_ver,
                "status": "生效中" if v.ver == et.version else "已替代",
                "by": v.by_name,
                "at": v.at,
                "change": v.change_note,
            }
            for v in await self.repo.versions(et.id)
        ]


DAILY_DAYS = 14


def mmdd(d: Any) -> str:
    return d.strftime("%m-%d")


def p95(sorted_ms: list[int]) -> int | None:
    """只统计已有处理时延；未知不能表示为0毫秒。"""
    return sorted_ms[int(len(sorted_ms) * 0.95)] if sorted_ms else None


def event_dict(e: Event, type_code: str) -> dict[str, Any]:
    return {
        "id": e.code,
        "type_id": type_code,
        "at": e.occurred_at,
        "obj_kind": e.obj_kind,
        "obj_id": e.object_id,
        "obj_label": e.obj_label,
        "summary": e.summary,
        "outcome": e.outcome,
        "subscribers": e.subscriber_count,
        "dedup_key": e.dedup_key,
        "latency_ms": e.latency_ms,
        "source": e.source,
        "producer_type": e.producer_type,
        "producer_object_type": e.producer_object_type,
        "trace_id": e.trace_id,
        "ingested_at": e.ingested_at,
        "ontology_version": e.release.ver if e.release else None,
    }


class EventRuntimeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.types = EventTypeRepo(session)
        self.repo = EventRepo(session)

    async def events(
        self, type_id: str, range_: str, outcome: str | None, page: PageParams
    ) -> tuple[list[dict[str, Any]], int, PageParams]:
        """按真实事件发生时间查询滚动 24 小时 / 7 天，排除未来及测试事件。"""
        et = await self.types.get_by_code_or_404(type_id)
        until = clock.now()
        since = until - (timedelta(hours=24) if range_ == "24h" else timedelta(days=7))
        oc = EventOutcome(outcome) if outcome and outcome != ALL else None
        total = await self.repo.count(et.id, since, oc, until)
        p = page.clamp(total)
        rows = await self.repo.page(et.id, since, oc, p.offset, p.size, until)
        return [event_dict(r, et.code) for r in rows], total, p

    async def event(self, code: str) -> dict[str, Any]:
        """单条事件（事件抽屉，mock eventById）。字段同近期事件列表的一行；不存在 404。"""
        got = await self.repo.get_with_type_code(code)
        if got is None:
            raise NotFound(self.repo.not_found_message, details={"id": code})
        return event_dict(*got)

    async def stats(self, type_id: str) -> dict[str, Any]:
        """近 7 天真实事件健康；14 个业务日历日趋势从事件行汇总，缺日补 0。"""
        et = await self.types.get_by_code_or_404(type_id)
        until = clock.now()
        since = until - timedelta(days=7)
        counts = await self.repo.outcome_counts(et.id, since, until)
        start, end = clock.last_days(DAILY_DAYS)
        days = await self.repo.daily(et.id, start, end, until)
        return {
            "weekly": sum(counts.values()),
            "total": sum(counts.values()),
            "dedup": counts.get(EventOutcome.deduped, 0),
            "failed": counts.get(EventOutcome.failed, 0),
            "partial": counts.get(EventOutcome.partial, 0),
            "pending": counts.get(EventOutcome.pending, 0),
            "p95": p95(await self.repo.latencies(et.id, since, until)),
            "as_of": clock.local_now(),
            "daily": [
                {"date": mmdd(d), "value": days.get(d, 0)}
                for d in (start + timedelta(days=i) for i in range(DAILY_DAYS))
            ],
        }
