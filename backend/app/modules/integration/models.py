"""ORM 模型（表名 writeback_call / ext_system / reconcile_task，不加前缀；主键 id bigint，外键指向 id）。

本批只建 ext_system；writeback_call、reconcile_task 在后面的批次（integration_runs 阶段）建。
新增模型要在 app/db/models.py 里 import。
"""

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LabelEnum
from app.db.base import Base, CodeMixin, MutableTableMixin
from app.db.types import JsonType, enum_column


class ExtSystemStatus(LabelEnum):
    """外部系统状态（action-event-api §3.5）。"""

    ok = "正常"
    degraded = "降级"
    disabled = "停用"


class ExtSystem(CodeMixin, MutableTableMixin, Base):
    """外部系统（前端 extsys.ts EXT_SYSTEMS）。code = 前端 id（EXT-ERP…）。

    密钥不入本表明文：secret_ref（加密引用）等写接口批次再加；mock 里只有脱敏说明 authDetail。
    """

    __tablename__ = "ext_system"
    __table_args__ = (UniqueConstraint("name"), {"comment": "外部系统（integration 模块）"})

    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="名称（唯一）")
    vendor: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="基础地址")
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="接口 auth")
    auth_detail: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="脱敏说明（接口 authDetail）"
    )
    idempotent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idem_header: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="幂等请求头（接口 idemKey）"
    )
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    max_retry: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_text: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="重试说明原文（接口 retry）"
    )
    rate_limit: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="负责人姓名（原文）")
    status: Mapped[ExtSystemStatus] = mapped_column(enum_column(ExtSystemStatus, 16), nullable=False)
    env: Mapped[str] = mapped_column(String(16), nullable=False, comment="环境原文（生产/测试）")
    usage: Mapped[dict | None] = mapped_column(JsonType, comment="用途说明 {text, ops, actor?}（mock 原样）")
