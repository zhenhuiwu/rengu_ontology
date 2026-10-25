r"""事务性 outbox（基础层，PDE 定：事件由写方在**同一事务**里写 outbox，其它模块只订阅）。

    from app.common.outbox import emit
    await emit(session, "release.published", {...}, event_id=f"ontology_release:{ver}:release.published")

- 写方：只调 `emit`，不 commit（跟业务行同一事务，回滚则事件一起消失）；
- event_id 唯一（同一事件重复写报 DUPLICATE_KEY），订阅方按 event_id 去重；
- 分发：`app.jobs.dispatch_outbox`（PDE 实现）扫 `dispatched_at is null and available_at <= now`，
  投给 `subscribe(topic, handler)` 登记的订阅者，成功写 dispatched_at，失败 attempts+1、记 last_error；
- payload 字段名用 camelCase（和接口一致）。

已定主题：
- `release.published`：versioning.create_version 写（发布和回滚都写），payload
  `{ver, parentVer, kind: "release"|"rollback", rollbackOf, changeOrderIds}`。
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Index, Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core import clock
from app.db.base import Base
from app.db.types import JsonType

RELEASE_PUBLISHED = "release.published"


class OutboxEvent(Base):
    __tablename__ = "outbox_event"
    __table_args__ = (
        Index(
            "ix_outbox_event_pending",
            "available_at",
            postgresql_where=text("dispatched_at IS NULL"),
        ),
        {"comment": "事务性 outbox（写方同事务写入，dispatch_outbox 分发）"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(200), nullable=False, unique=True, comment="幂等键，订阅方去重"
    )
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=clock.now)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=clock.now)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)


async def emit(
    session: AsyncSession,
    topic: str,
    payload: dict[str, Any],
    *,
    event_id: str,
    available_at: datetime | None = None,
) -> OutboxEvent:
    """在调用方的事务里写一条事件（flush，不 commit）。"""
    now = clock.now()
    ev = OutboxEvent(
        event_id=event_id, topic=topic, payload=payload, created_at=now, available_at=available_at or now
    )
    session.add(ev)
    await session.flush()
    return ev


Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
_SUBSCRIBERS: dict[str, list[Handler]] = {}


def subscribe(topic: str, handler: Handler) -> None:
    """模块启动时登记订阅（只订阅，不写别的模块的表）。dispatch_outbox 调用。"""
    _SUBSCRIBERS.setdefault(topic, []).append(handler)


def subscribers(topic: str) -> list[Handler]:
    return list(_SUBSCRIBERS.get(topic, []))
