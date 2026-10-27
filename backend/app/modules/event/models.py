"""事件类型定义表 + 事件运行表（action-event-api §9）。

- event（近期事件）、event_daily_stat（趋势）、event_dedup（持久去重）与
  event_delivery（逐订阅方投递）；新入站同时保存不可变定义快照。

- 主键代理键 id bigint，外键指向 id；接口只出业务编码（event_type.code = 前端 id）。
- 规则引用（event_type.rule_code、event_subscription.rule_code）暂为**软引用**（varchar R-…），
  动态 rule 合并后改 rule_id → rule.id 外键。
- weekly（近 7 天事件数 / 订阅方投递数）文档里归统计表 event_daily_stat / event_subscription_weekly_stat，
  统计表不在本批，暂作为列存在定义表上 [推断]；不进快照。
新增模型要在 app/db/models.py 里 import。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import LabelEnum
from app.db.base import (
    AppendOnlyTableMixin,
    Base,
    CodeMixin,
    IdMixin,
    MutableTableMixin,
    OntologyVersionMixin,
)
from app.db.types import JsonType, enum_column
from app.modules.action.models import TypeStatus
from app.modules.versioning.models import OntologyRelease


class EventSource(LabelEnum):
    """事件来源（§3.5）。成员顺序 = 前端 EVENT_SOURCES 顺序。"""

    sync = "数据同步"
    rule = "规则"
    action = "动作"
    external = "外部系统"


class SubscriberKind(LabelEnum):
    rule = "规则"
    automation = "自动化"
    notify = "通知"
    external = "外部订阅"


class SubscriberStatus(LabelEnum):
    enabled = "启用"
    disabled = "停用"
    error = "异常"


class EventType(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "event_type"
    __table_args__ = (UniqueConstraint("api"), {"comment": "事件类型（当前版本）"})

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[EventSource] = mapped_column(enum_column(EventSource, 16), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rule_code: Mapped[str | None] = mapped_column(String(32), comment="来源规则 R-…（软引用，接口 ruleId）")
    object: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    object_api: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    obj_kind: Mapped[str | None] = mapped_column(String(64), comment="对象类型前端 id（软引用）")
    status: Mapped[TypeStatus] = mapped_column(enum_column(TypeStatus, 16), nullable=False)
    version: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 desc")
    dedup_key_rule: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 dedupKey")
    dedup_window: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="原文")
    delivery: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    ordering: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, comment="接口 retention = 'N 天'")
    weekly: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="近 7 天事件数 [推断]")
    retired_release_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("ontology_release.id"),
        comment="整版回滚下线于哪一版（ontology_release.id）",
    )

    payload: Mapped[list["EventPayloadField"]] = relationship(
        lazy="selectin", order_by="EventPayloadField.seq"
    )
    subscribers: Mapped[list["EventSubscription"]] = relationship(
        lazy="selectin", order_by="EventSubscription.seq"
    )


class EventPayloadField(IdMixin, MutableTableMixin, Base):
    """事件自身载荷字段（EVENT_TYPES[].specific）。"""

    __tablename__ = "event_payload_field"
    __table_args__ = (UniqueConstraint("event_type_id", "api"), {"comment": "事件载荷字段"})

    event_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 desc")
    sample: Mapped[str] = mapped_column(Text, nullable=False, default="")


class EventEnvelopeField(IdMixin, MutableTableMixin, Base):
    """通用信封字段（全局 10 行，只读；payloadOf 结果的最后 10 项）。"""

    __tablename__ = "event_envelope_field"
    __table_args__ = (UniqueConstraint("api"), {"comment": "事件信封字段（全局）"})

    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    api: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sample: Mapped[str] = mapped_column(Text, nullable=False, default="")


class EventSubscription(IdMixin, MutableTableMixin, Base):
    __tablename__ = "event_subscription"
    __table_args__ = (UniqueConstraint("event_type_id", "name"), {"comment": "事件订阅方"})

    event_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[SubscriberKind] = mapped_column(enum_column(SubscriberKind, 16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_code: Mapped[str | None] = mapped_column(String(32), comment="规则 R-…（软引用，接口 ruleId）")
    filter_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 filter")
    filter_expr: Mapped[dict | None] = mapped_column(JsonType)
    then_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="接口 then")
    status: Mapped[SubscriberStatus] = mapped_column(enum_column(SubscriberStatus, 16), nullable=False)
    weekly: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="近 7 天投递数 [推断]")


class EventTypeVersion(IdMixin, AppendOnlyTableMixin, Base):
    """同 action_type_version：只追加、不存状态；release_ver 软引用。"""

    __tablename__ = "event_type_version"
    __table_args__ = (UniqueConstraint("event_type_id", "ver"), {"comment": "事件类型版本（只追加）"})

    event_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("event_type.id"), nullable=False)
    ver: Mapped[str] = mapped_column(String(16), nullable=False)
    release_ver: Mapped[str] = mapped_column(String(32), nullable=False, comment="本体发布版本号（软引用）")
    change_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    by_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    definition: Mapped[dict | None] = mapped_column(JsonType)


class EventOutcome(LabelEnum):
    """运行结果；pending仅用于真实入站，尚未调用任何订阅方。"""

    delivered = "已投递"
    alert_created = "已生成告警"
    deduped = "已去重"
    partial = "部分投递"
    failed = "投递失败"
    pending = "待投递"


class Event(CodeMixin, MutableTableMixin, OntologyVersionMixin, Base):
    """一条事件（mock EVENTS 的一行 = eventsOf() 并集，1,243 条）。code = 生产方 eventId（seed 为 EV-…）。
    第一期不分区；按 (event_type_id, occurred_at desc, id) 索引支持页码分页。"""

    __tablename__ = "event"
    __table_args__ = (
        Index("ix_event_event_type_id_occurred_at_id", "event_type_id", "occurred_at", "id"),
        {"comment": "事件（近期事件流）"},
    )

    event_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="接口 at")
    obj_kind: Mapped[str | None] = mapped_column(String(64), comment="store/product/order/action，可空")
    object_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="接口 objId")
    obj_label: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="载荷摘要")
    outcome: Mapped[EventOutcome] = mapped_column(enum_column(EventOutcome, 16), nullable=False)
    subscriber_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="接口 subscribers"
    )
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JsonType, comment="原始载荷（seed 无）")
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source: Mapped[str | None] = mapped_column(String(128))
    producer_type: Mapped[str | None] = mapped_column(String(64))
    producer_object_type: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    envelope_hash: Mapped[str | None] = mapped_column(String(64))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    definition_snapshot: Mapped[dict | None] = mapped_column(JsonType)
    release: Mapped["OntologyRelease | None"] = relationship(lazy="joined")


class EventDelivery(IdMixin, MutableTableMixin, Base):
    """One durable delivery per immutable event/subscriber; configuration never lives here."""

    __tablename__ = "event_delivery"
    __table_args__ = (
        UniqueConstraint("event_id", "subscriber_name"),
        Index("ix_event_delivery_state_next_at", "state", "next_at"),
    )
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("event.id"), nullable=False)
    subscriber_name: Mapped[str] = mapped_column(String(128), nullable=False)
    subscription: Mapped[dict] = mapped_column(JsonType, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict | None] = mapped_column(JsonType)


class EventDeliveryRecord(IdMixin, AppendOnlyTableMixin, Base):
    """Immutable attempt and human decision journal; never stores transport secrets."""

    __tablename__ = "event_delivery_record"
    __table_args__ = (Index("ix_event_delivery_record_delivery_id_id", "delivery_id", "id"),)
    delivery_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("event_delivery.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict | None] = mapped_column(JsonType)
    detail: Mapped[dict | None] = mapped_column(JsonType)


class EventDedup(IdMixin, MutableTableMixin, Base):
    """每种类型/去重键的最近接受时间；不随事件保留期清除，因此永久窗口不会失效。"""

    __tablename__ = "event_dedup"
    __table_args__ = (UniqueConstraint("event_type_id", "dedup_key"),)

    event_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False
    )
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)
    last_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventDailyStat(IdMixin, MutableTableMixin, Base):
    """事件类型按天的事件数（mock eventDaily()，和明细对不上，按 §7.1.2 原样入库）。"""

    __tablename__ = "event_daily_stat"
    __table_args__ = (UniqueConstraint("event_type_id", "stat_date"), {"comment": "事件类型按天统计"})

    event_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
