from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedColumnsMixin, IdMixin


class Notification(IdMixin, CreatedColumnsMixin, Base):
    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("source_event_id", "recipient_id"),
        Index("ix_notification_recipient_recent", "ontology_id", "recipient_id", "created_at", "id"),
        Index("ix_notification_recipient_unread", "ontology_id", "recipient_id", "read_at"),
    )

    recipient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    source_event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cr_event.id", ondelete="CASCADE"), nullable=False
    )
    cr_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("change_request.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
