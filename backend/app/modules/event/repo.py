"""数据访问（事件类型 + 事件运行表）。"""

from collections.abc import Sequence
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select

from app.common.repository import BaseRepository
from app.core.config import get_settings
from app.modules.event.models import (
    Event,
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
        """按事件编号（EV-…）取一条事件和它的事件类型编码，含可直接查看的测试事件。"""
        stmt = (
            select(Event, EventType.code)
            .join(EventType, EventType.id == Event.event_type_id)
            .where(Event.code == code)
        )
        row = (await self.session.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    @staticmethod
    def _filter(
        type_id: int, since: datetime | None, outcome: EventOutcome | None, until: datetime | None = None
    ) -> Select[tuple[Event]]:
        stmt = select(Event).where(Event.event_type_id == type_id, Event.is_test.is_(False))
        if since is not None:
            stmt = stmt.where(Event.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(Event.occurred_at <= until)
        if outcome is not None:
            stmt = stmt.where(Event.outcome == outcome)
        return stmt.order_by(Event.occurred_at.desc(), Event.id.desc())

    async def count(
        self,
        type_id: int,
        since: datetime | None,
        outcome: EventOutcome | None,
        until: datetime | None = None,
    ) -> int:
        stmt = self._filter(type_id, since, outcome, until).order_by(None)
        return int(await self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)

    async def page(
        self,
        type_id: int,
        since: datetime | None,
        outcome: EventOutcome | None,
        offset: int,
        limit: int,
        until: datetime | None = None,
    ) -> Sequence[Event]:
        stmt = self._filter(type_id, since, outcome, until).offset(offset).limit(limit)
        return (await self.session.scalars(stmt)).all()

    async def outcome_counts(
        self, type_id: int, since: datetime | None = None, until: datetime | None = None
    ) -> dict[EventOutcome, int]:
        rows = self._filter(type_id, since, None, until).order_by(None).subquery()
        stmt = select(rows.c.outcome, func.count()).group_by(rows.c.outcome)
        return {o: int(n) for o, n in (await self.session.execute(stmt)).all()}

    async def latencies(
        self, type_id: int, since: datetime | None = None, until: datetime | None = None
    ) -> list[int]:
        rows = self._filter(type_id, since, None, until).order_by(None).subquery()
        stmt = (
            select(rows.c.latency_ms)
            .where(rows.c.latency_ms.is_not(None), rows.c.outcome != EventOutcome.pending)
            .order_by(rows.c.latency_ms)
        )
        return list((await self.session.scalars(stmt)).all())

    async def daily(self, type_id: int, start: date, end: date, until: datetime) -> dict[date, int]:
        zone = get_settings().display_tz
        since = datetime.combine(start, time.min, ZoneInfo(zone))
        rows = self._filter(type_id, since, None, until).order_by(None).subquery()
        day = func.date(func.timezone(zone, rows.c.occurred_at))
        stmt = select(day, func.count()).where(day <= end).group_by(day)
        return {d: int(v) for d, v in (await self.session.execute(stmt)).all()}
