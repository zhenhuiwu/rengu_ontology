"""事件类型读取（同 action.service：当前读展开表，`?ver=` 读快照 eventTypes 段）。

weekly（列表 / 详情 / 订阅方的近 7 天次数）是运行统计，不分版本，始终取当前表里的值
（seed 来自 mock；以后由事件统计任务刷新）[推断]。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import ViewContext
from app.modules.action.service import ALL, defs_for_view, find
from app.modules.event.models import EventSource, EventType
from app.modules.event.repo import EventTypeRepo
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
        return {**d, "weekly": cur.weekly if cur else 0}

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
