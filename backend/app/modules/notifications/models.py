from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedColumnsMixin, IdMixin


class Notification(IdMixin, CreatedColumnsMixin, Base):
    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("source_event_id", "recipient_id"),
        UniqueConstraint("source_outbox_id", "recipient_id"),
        CheckConstraint(
            "(source_kind='cr' AND source_event_id IS NOT NULL AND cr_id IS NOT NULL "
            "AND source_outbox_id IS NULL) OR (source_kind='page' AND source_event_id IS NULL "
            "AND cr_id IS NULL AND source_outbox_id IS NOT NULL AND target_code IS NOT NULL)",
            name="notification_source",
        ),
        Index("ix_notification_recipient_recent", "ontology_id", "recipient_id", "created_at", "id"),
        Index("ix_notification_recipient_unread", "ontology_id", "recipient_id", "read_at"),
    )

    recipient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    source_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("cr_event.id", ondelete="CASCADE")
    )
    cr_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("change_request.id", ondelete="CASCADE"))
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="cr", server_default="cr")
    source_outbox_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("outbox_event.id", ondelete="CASCADE")
    )
    target_code: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
