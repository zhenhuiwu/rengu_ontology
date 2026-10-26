"""事件类型读取（同 action.service：当前读展开表，`?ver=` 读快照 eventTypes 段）。

EventRuntimeService（palantir-r2）：近期事件 / 运行健康 / 近 14 天趋势，读 event、event_daily_stat。
运行数据不分版本：`?ver=` 不影响，按当前 event_type 表找类型（不存在 404）[推断]。

weekly（列表 / 详情 / 订阅方的近 7 天次数）是运行统计，不分版本，始终取当前表里的值
（seed 来自 mock；以后由事件统计任务刷新）[推断]。
"""

from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import ViewContext
from app.common.pagination import PageParams
from app.core import clock
from app.modules.action.service import ALL, defs_for_view, find
from app.modules.event.models import Event, EventOutcome, EventSource, EventType
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
        return await defs_for_view(self.session, ctx, await self.rows(), SECTION, event_def)

    async def one(self, ctx: ViewContext, type_id: str) -> dict[str, Any]:
        return find(await self.defs(ctx), type_id, "事件类型不存在")

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
        cur = next((r for r in await self.rows() if r.code == type_id), None)
        return {**d, "weekly": cur.weekly if cur else 0, "specific": d.get("payload") or []}

    async def payload_fields(self, ctx: ViewContext, type_id: str) -> tuple[list[dict[str, Any]], int]:
        """payloadOf()：专有字段 + 通用信封字段（envelope=true）。"""
        d = await self.one(ctx, type_id)
        env = [{**field_dict(f), "envelope": True} for f in await self.repo.envelope()]
        specific = [{**f, "envelope": False} for f in d.get("payload") or []]
        return specific + env, len(env)

    async def subscribers(self, ctx: ViewContext, type_id: str) -> list[dict[str, Any]]:
        d = await self.one(ctx, type_id)
        cur = next((r for r in await self.rows() if r.code == type_id), None)
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


def p95(sorted_ms: list[int]) -> int:
    """同前端 health.p95：按延迟升序取下标 floor(n × 0.95)；没有事件 = 0。"""
    return sorted_ms[int(len(sorted_ms) * 0.95)] if sorted_ms else 0


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
    }


class EventRuntimeService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.types = EventTypeRepo(session)
        self.repo = EventRepo(session)

    async def events(
        self, type_id: str, range_: str, outcome: str | None, page: PageParams
    ) -> tuple[list[dict[str, Any]], int, PageParams]:
        """range：24h = 近 24 小时（clock.now() − 24h 起）；7d = 不设下限 [推断]——
        前端 mock 的「近 7 天」不按时间过滤（eventsOf 全部样本，低频类型有 7 天前的），前端为准。"""
        et = await self.types.get_by_code_or_404(type_id)
        since = clock.now() - timedelta(hours=24) if range_ == "24h" else None
        oc = EventOutcome(outcome) if outcome and outcome != ALL else None
        total = await self.repo.count(et.id, since, oc)
        p = page.clamp(total)
        rows = await self.repo.page(et.id, since, oc, p.offset, p.size)
        return [event_dict(r, et.code) for r in rows], total, p

    async def stats(self, type_id: str) -> dict[str, Any]:
        """运行健康按该类型全部事件算（同前端 health，口径 = 近期事件页签「近 7 天」）；
        daily = 截至今天的 14 天，缺的天补 0。"""
        et = await self.types.get_by_code_or_404(type_id)
        counts = await self.repo.outcome_counts(et.id)
        start, end = clock.last_days(DAILY_DAYS)
        days = await self.repo.daily(et.id, start, end)
        return {
            "weekly": et.weekly,
            "total": sum(counts.values()),
            "dedup": counts.get(EventOutcome.deduped, 0),
            "failed": counts.get(EventOutcome.failed, 0),
            "partial": counts.get(EventOutcome.partial, 0),
            "p95": p95(await self.repo.latencies(et.id)),
            "as_of": clock.local_now(),
            "daily": [
                {"date": mmdd(d), "value": days.get(d, 0)}
                for d in (start + timedelta(days=i) for i in range(DAILY_DAYS))
            ],
        }
