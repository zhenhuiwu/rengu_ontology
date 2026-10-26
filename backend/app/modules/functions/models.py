"""Function versions are separate from ontology release snapshots."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AppendOnlyTableMixin, Base, CodeMixin, IdMixin, MutableTableMixin
from app.db.types import JsonType


class Function(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "function"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    code_text: Mapped[str] = mapped_column(Text, nullable=False)
    sample_in: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    sample_out: Mapped[Any | None] = mapped_column(JsonType)
    avg_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    calls_7d: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class FnParam(IdMixin, MutableTableMixin, Base):
    __tablename__ = "fn_param"
    __table_args__ = (UniqueConstraint("function_id", "direction", "seq"),)

    function_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("function.id"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(3), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    obj_type: Mapped[str | None] = mapped_column(String(128))
    desc: Mapped[str] = mapped_column(Text, nullable=False)


class FnUse(IdMixin, MutableTableMixin, Base):
    __tablename__ = "fn_use"

    function_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("function.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class FnVersion(IdMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "fn_version"
    __table_args__ = (UniqueConstraint("function_id", "ver"),)

    function_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("function.id"), nullable=False, index=True
    )
    ver: Mapped[str] = mapped_column(String(32), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
