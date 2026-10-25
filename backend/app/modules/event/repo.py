"""数据访问（事件类型）。"""

from collections.abc import Sequence

from sqlalchemy import select

from app.common.repository import BaseRepository
from app.modules.event.models import EventEnvelopeField, EventType, EventTypeVersion


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
