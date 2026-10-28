"""ORM 模型（表名 writeback_call / ext_system / reconcile_task，不加前缀；主键 id bigint，外键指向 id）。

ext_system（integration_0001）、writeback_call（integration_0002）、reconcile_task（integration_0013）。
新增模型要在 app/db/models.py 里 import。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LabelEnum
from app.db.base import Base, CodeMixin, IdMixin, MutableTableMixin
from app.db.types import JsonType, enum_column

NO_AUTH = "无需授权"


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
    local_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    action_authorizations: Mapped[dict | None] = mapped_column(JsonType)
    usage: Mapped[dict | None] = mapped_column(JsonType, comment="用途说明 {text, ops, actor?}（mock 原样）")


class CallResult(LabelEnum):
    """外部调用结果（action-event-api §3.5：succeeded→成功，failed→失败，unknown→结果未知）。"""

    succeeded = "成功"
    failed = "失败"
    unknown = "结果未知"


class CallKind(LabelEnum):
    """调用类型（§9.1）：写回 / 查询 / 外部系统页测试调用。接口不出这个字段。"""

    write = "写回"
    query = "查询"
    test = "测试"


class WritebackCall(CodeMixin, MutableTableMixin, Base):
    """外部系统调用记录（前端 extsys.ts extCalls() 的一行 = 一次业务调用，重试计入 attempt）。

    code = 前端 id（CRM-00090 / 测试调用 CRM-T…）。列和前端 ExtCall 一一对应：
    sysId→sys_id、time→at、action→op_name、target→target_label、ms→elapsed_ms、http→http_status、runId→action_run_id。
    可变表：`:mark-checked` 改 result / msg。
    """

    __tablename__ = "writeback_call"
    __table_args__ = (
        Index("ix_writeback_call_sys_at", "sys_id", "at"),
        {"comment": "外部系统调用记录（integration 模块）"},
    )

    sys_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ext_system.id"), nullable=False)
    action_run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("action_run.id"), index=True, comment="执行记录（接口 runId）"
    )
    kind: Mapped[CallKind] = mapped_column(enum_column(CallKind, 16), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="调用时间（接口 time）"
    )
    op_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="接口 action")
    target_label: Mapped[str] = mapped_column(String(200), nullable=False, comment="接口 target")
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    idem_key: Mapped[str | None] = mapped_column(String(128), comment="幂等键；不支持幂等为空")
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="接口 ms")
    http_status: Mapped[int | None] = mapped_column(Integer, comment="接口 http；超时为空")
    result: Mapped[CallResult] = mapped_column(enum_column(CallResult, 16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, comment="发起人原文")
    msg: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ReconcileState(LabelEnum):
    querying = "查询中"
    resolved = "已有结论"
    manual = "转人工"


class ReconcileTask(IdMixin, MutableTableMixin, Base):
    """One durable reconciliation cycle per execution; retries never resubmit a write."""

    __tablename__ = "reconcile_task"
    __table_args__ = (
        UniqueConstraint("action_run_id"),
        Index("ix_reconcile_task_state_next_at", "state", "next_at"),
    )

    action_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("action_run.id"), nullable=False)
    sys_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ext_system.id"), nullable=False)
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[ReconcileState] = mapped_column(enum_column(ReconcileState, 16), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_ext_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciler: Mapped[str | None] = mapped_column(String(64))
    reconcile_result: Mapped[str | None] = mapped_column(String(16))
