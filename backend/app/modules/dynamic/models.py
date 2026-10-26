"""动态本体：告警 + 动作中心（alert / alert_log / action_run / action_run_log）。

外键约定（团队约定 2026-10-25）：只指向骨架里已有的表和本模块自己的表。
还没合并的模块（规则 rule / rule_hit / 推演 scenario 是本模块后续迭代；
action_type / ext_system 属于 Palantir 模块）一律用**业务编号软引用**（*_code 列），
见 docs/backend/dynamic-impl-notes.md「FK to add later」。

人员字段（assignee / initiator / confirmer / approver / actor）按前端 mock 存姓名字符串。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LabelEnum, TimelineStatus
from app.db.base import AppendOnlyTableMixin, Base, CodeMixin, IdMixin, MutableTableMixin, unique_except_seed
from app.db.types import JsonType, enum_column


class AlertLevel(LabelEnum):
    high = "高"
    medium = "中"
    low = "低"


class AlertStatus(LabelEnum):
    open = "待处理"
    processing = "处理中"
    resolved = "已解决"
    ignored = "已忽略"


class AlertOp(LabelEnum):
    """告警日志的操作类型（[推断] 前端日志只有文本，op 由文本推出，接口不输出）。"""

    created = "created"
    claim = "claim"
    transfer = "transfer"
    close = "close"
    start_action = "start_action"
    action_result = "action_result"
    note = "note"


class ActionStatus(LabelEnum):
    staged = "待确认"
    approving = "审批中"
    executing = "执行中"
    succeeded = "成功"
    failed = "失败"
    unknown = "结果未知"
    withdrawn = "已撤回"
    reconciled = "已对账"


class SourceType(LabelEnum):
    rule = "规则"
    alert = "告警"
    alert_ops = "告警处理"
    qa = "问数"
    manual = "手动"
    scenario = "推演"


class ReconcileResult(LabelEnum):
    succeeded = "外部已成功"
    not_executed = "外部未执行"


IN_FLIGHT = (ActionStatus.staged, ActionStatus.approving, ActionStatus.executing, ActionStatus.unknown)


class Alert(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "alert"
    __table_args__ = (
        Index("ix_alert_rule_obj", "rule_code", "obj_id"),
        {"comment": "告警（规则命中生成，人工处理）"},
    )

    rule_code: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="规则编号（软引用 rule.code，FK to add later）"
    )
    rule_version: Mapped[str | None] = mapped_column(String(16), comment="命中时的规则版本（v3）")
    hit_code: Mapped[str | None] = mapped_column(
        String(64), comment="命中记录编号（软引用 rule_hit，FK to add later）"
    )
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    level: Mapped[AlertLevel] = mapped_column(enum_column(AlertLevel, 16), nullable=False)
    obj_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="product/store/order（软引用对象类型）"
    )
    obj_id: Mapped[str] = mapped_column(String(64), nullable=False)
    obj_label: Mapped[str] = mapped_column(String(200), nullable=False)
    dims: Mapped[list] = mapped_column(
        JsonType,
        nullable=False,
        default=list,
        comment="通用维度值 [{objKind,id,label}]（来自规则去重键，如所属门店；列按维度生成）",
    )
    value: Mapped[str] = mapped_column(String(200), nullable=False, comment="命中值（展示文本）")
    threshold: Mapped[str] = mapped_column(String(200), nullable=False, comment="阈值（展示文本）")
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AlertStatus] = mapped_column(enum_column(AlertStatus, 16), nullable=False, index=True)
    assignee: Mapped[str | None] = mapped_column(String(64), comment="处理人姓名")
    close_reason: Mapped[str | None] = mapped_column(Text)
    action_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("action_run.id", use_alter=True),
        comment="关联动作（action_run.id；和 action_run.alert_id 互指，建表后再加约束）",
    )


class AlertLog(IdMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "alert_log"
    __table_args__ = (UniqueConstraint("alert_id", "seq"), {"comment": "告警处理日志（只追加）"})

    alert_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("alert.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False, comment="同一告警内的顺序（1 起）")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, comment="操作人姓名 / 系统")
    op: Mapped[AlertOp] = mapped_column(enum_column(AlertOp, 16), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), comment="AlertStatus code")
    to_status: Mapped[str | None] = mapped_column(String(16), comment="AlertStatus code")
    abnormal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class ActionRunDailyCounter(IdMixin, MutableTableMixin, Base):
    """内部日计数：独立短事务发号，业务回滚不复用已发给外部的编号。"""

    __tablename__ = "action_run_daily_counter"

    day: Mapped[str] = mapped_column(String(8), nullable=False, unique=True)
    last_value: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ActionRun(CodeMixin, MutableTableMixin, Base):
    """动作执行记录：动作中心 ACTIONS + 动作类型页 RUNS 合并成一张表。"""

    __tablename__ = "action_run"
    __table_args__ = (
        unique_except_seed("action_run", "idem_key"),
        Index("ix_action_run_center_status", "in_center", "status"),
        {"comment": "动作执行记录（动作中心 + 动作类型执行记录）"},
    )

    action_type_code: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="动作类型编号（软引用 action_type，FK to add later）"
    )
    action_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="动作名称（前端 action）")
    type_version: Mapped[str | None] = mapped_column(String(16))
    source_type: Mapped[SourceType] = mapped_column(enum_column(SourceType, 16), nullable=False)
    source_label: Mapped[str] = mapped_column(String(16), nullable=False, comment="来源原文")
    rule_code: Mapped[str | None] = mapped_column(
        String(64), index=True, comment="软引用 rule，FK to add later"
    )
    rule_version: Mapped[str | None] = mapped_column(String(16))
    alert_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("alert.id"), index=True)
    qa_session: Mapped[str | None] = mapped_column(String(64), comment="问数会话（软引用，FK to add later）")
    qa_session_title: Mapped[str | None] = mapped_column(String(200))
    scenario_code: Mapped[str | None] = mapped_column(String(64), comment="软引用 scenario，FK to add later")
    scenario_name: Mapped[str | None] = mapped_column(String(200))
    obj_kind: Mapped[str | None] = mapped_column(String(32))
    obj_id: Mapped[str | None] = mapped_column(String(64), index=True)
    obj_label: Mapped[str | None] = mapped_column(String(200))
    params: Mapped[dict | None] = mapped_column(JsonType)
    params_text: Mapped[str | None] = mapped_column(Text, comment="参数摘要（ACTIONS.params / RUNS.summary）")
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    result_code: Mapped[str | None] = mapped_column(String(200))
    retry_count: Mapped[int | None] = mapped_column(Integer)
    initiator: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmer: Mapped[str | None] = mapped_column(String(64))
    approver: Mapped[str | None] = mapped_column(String(64))
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    biz_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[ActionStatus] = mapped_column(enum_column(ActionStatus, 16), nullable=False, index=True)
    sys_code: Mapped[str | None] = mapped_column(
        String(64), comment="外部系统编号（软引用 ext_system，FK to add later）"
    )
    ext_ref: Mapped[str | None] = mapped_column(String(128))
    fail_reason: Mapped[str | None] = mapped_column(Text)
    unknown_reason: Mapped[str | None] = mapped_column(Text)
    reconcile_result: Mapped[ReconcileResult | None] = mapped_column(enum_column(ReconcileResult, 16))
    reconcile_note: Mapped[str | None] = mapped_column(Text)
    reconcile_by: Mapped[str | None] = mapped_column(String(64))
    withdraw_reason: Mapped[str | None] = mapped_column(Text)
    retry_of_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("action_run.id"))
    retried_as_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("action_run.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    in_center: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="[推断] 是否出现在动作中心（ACTIONS 与新建为 true；RUNS 历史为 false）",
    )


class ActionRunLog(IdMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "action_run_log"
    __table_args__ = (UniqueConstraint("action_run_id", "seq"), {"comment": "动作执行时间线（只追加）"})

    action_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("action_run.id"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str | None] = mapped_column(Text, comment="前端 ActionLog.desc 原文（说明 / 操作人）")
    level: Mapped[TimelineStatus | None] = mapped_column(
        enum_column(TimelineStatus, 16), comment="前端 status"
    )
