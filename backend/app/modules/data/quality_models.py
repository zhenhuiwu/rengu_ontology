"""我的质量检查定义和只追加的真实运行记录。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AppendOnlyTableMixin, Base, CodeMixin, IdMixin, MutableTableMixin
from app.db.types import JsonType


class QualityCheck(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "quality_check"
    __table_args__ = (UniqueConstraint("code"),)
    object_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("object_type.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    threshold: Mapped[str] = mapped_column(String(128), nullable=False)
    limit_value: Mapped[float] = mapped_column(Float, nullable=False)
    schedule: Mapped[str] = mapped_column(String(32), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QualityRun(IdMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "quality_run"
    check_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("quality_check.id"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    actual: Mapped[str] = mapped_column(Text, nullable=False)
    ms: Mapped[int] = mapped_column(Integer, nullable=False)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    definition: Mapped[dict] = mapped_column(JsonType, nullable=False)
