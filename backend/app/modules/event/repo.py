"""数据访问（事件类型 + 事件运行表）。"""

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import Select, func, select

from app.common.repository import BaseRepository
from app.modules.event.models import (
    Event,
    EventDailyStat,
    EventEnvelopeField,
    EventOutcome,
    EventType,
    EventTypeVersion,
)


class EventTypeRepo(BaseRepository[EventType]):
    model = EventType
    not_found_message = "事件类型不存在"

    async def all_ordered(self) -> Sequence[EventType]:
        return (await self.session.scalars(select(EventType).order_by(EventType.id))).all()

    async def envelope(self) -> Sequence[EventEnvelopeField]:
        return (await self.session.scalars(select(EventEnvelopeField).order_by(EventEnvelopeField.seq))).all()

    async def versions(self, type_id: int) -> Sequence[EventTypeVersion]:
        stmt = (
            select(EventTypeVersion)
            .where(EventTypeVersion.event_type_id == type_id)
            .order_by(EventTypeVersion.at.desc(), EventTypeVersion.id.desc())
        )
        return (await self.session.scalars(stmt)).all()


class EventRepo(BaseRepository[Event]):
    model = Event
    not_found_message = "事件不存在"

    async def get_with_type_code(self, code: str) -> tuple[Event, str] | None:
        """按事件编号（EV-…）取一条事件和它的事件类型编码；测试事件不算（同近期事件列表）。"""
        stmt = (
            select(Event, EventType.code)
            .join(EventType, EventType.id == Event.event_type_id)
            .where(Event.code == code, Event.is_test.is_(False))
        )
        row = (await self.session.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    @staticmethod
    def _filter(type_id: int, since: datetime | None, outcome: EventOutcome | None) -> Select[tuple[Event]]:
        stmt = select(Event).where(Event.event_type_id == type_id, Event.is_test.is_(False))
        if since is not None:
            stmt = stmt.where(Event.occurred_at >= since)
        if outcome is not None:
            stmt = stmt.where(Event.outcome == outcome)
        return stmt.order_by(Event.occurred_at.desc(), Event.id.desc())

    async def count(self, type_id: int, since: datetime | None, outcome: EventOutcome | None) -> int:
        stmt = self._filter(type_id, since, outcome).order_by(None)
        return int(await self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    async def page(
        self, type_id: int, since: datetime | None, outcome: EventOutcome | None, offset: int, limit: int
    ) -> Sequence[Event]:
        stmt = self._filter(type_id, since, outcome).offset(offset).limit(limit)
        return (await self.session.scalars(stmt)).all()

    async def outcome_counts(self, type_id: int) -> dict[EventOutcome, int]:
        stmt = (
            select(Event.outcome, func.count())
            .where(Event.event_type_id == type_id, Event.is_test.is_(False))
            .group_by(Event.outcome)
        )
        return {o: int(n) for o, n in (await self.session.execute(stmt)).all()}

    async def latencies(self, type_id: int) -> list[int]:
        stmt = (
            select(Event.latency_ms)
            .where(Event.event_type_id == type_id, Event.is_test.is_(False))
            .order_by(Event.latency_ms)
        )
        return list((await self.session.scalars(stmt)).all())

    async def daily(self, type_id: int, start: date, end: date) -> dict[date, int]:
        stmt = select(EventDailyStat.stat_date, EventDailyStat.value).where(
            EventDailyStat.event_type_id == type_id,
            EventDailyStat.stat_date >= start,
            EventDailyStat.stat_date <= end,
        )
        return {d: int(v) for d, v in (await self.session.execute(stmt)).all()}
