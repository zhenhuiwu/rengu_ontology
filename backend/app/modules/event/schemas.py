"""对外 Schema（字段名 = 前端 mock：EVENT_TYPES / payloadOf / subscribersOf / versionsOf）。"""

from app.core.schema import ApiModel, DateTimeMin, DateTimeSec
from app.modules.action.models import TypeStatus
from app.modules.event.models import EventOutcome, EventSource, SubscriberKind, SubscriberStatus


class EventTypeOut(ApiModel):
    id: str
    name: str
    api: str
    source: EventSource
    status: TypeStatus
    version: str
    object: str


class EventTypeListOut(ApiModel):
    items: list[EventTypeOut]
    sources: list[str]
    total: int
    page: int
    size: int


class FieldOut(ApiModel):
    name: str
    api: str
    type: str
    required: bool
    desc: str
    sample: str


class EventTypeDetailOut(EventTypeOut):
    source_ref: str
    rule_id: str | None = None
    object_api: str
    obj_kind: str | None = None
    owner: str
    desc: str
    dedup_key: str
    dedup_window: str
    delivery: str
    ordering: str
    retention: str
    weekly: int
    specific: list[FieldOut] = []
    """事件自身载荷字段（= mock EVENT_TYPES[].specific，不含信封字段）；页面草稿编辑和信封区分都按它算。"""


class PayloadFieldOut(FieldOut):
    envelope: bool


class PayloadFieldPageOut(ApiModel):
    items: list[PayloadFieldOut]
    envelope_count: int
    total: int
    page: int
    size: int


class SubscriberOut(ApiModel):
    kind: SubscriberKind
    name: str
    rule_id: str | None = None
    filter: str
    then: str
    status: SubscriberStatus
    weekly: int


# ---- 运行数据（palantir-r2；前端 EventRecord / EventTypes.vue health + eventDaily） ----


class EventOut(ApiModel):
    """mock EventRecord。objKind 可空（告警 / 会员类事件没有），前端 clean() 收成可选。"""

    id: str
    type_id: str
    at: DateTimeSec
    obj_kind: str | None = None
    obj_id: str
    obj_label: str
    summary: str
    outcome: EventOutcome
    subscribers: int
    dedup_key: str
    latency_ms: int | None
    source: str | None = None
    producer_type: str | None = None
    producer_object_type: str | None = None
    trace_id: str | None = None
    ingested_at: DateTimeSec | None = None
    ontology_version: str | None = None


class EventPageOut(ApiModel):
    items: list[EventOut]
    total: int
    page: int
    size: int


class DailyValueOut(ApiModel):
    date: str  # "MM-DD"（mock eventDaily）
    value: int


class EventStatsOut(ApiModel):
    """EventTypes.vue 概览「运行健康」+「近 14 天事件数」。"""

    weekly: int
    total: int
    dedup: int
    failed: int
    partial: int
    pending: int = 0
    p95: int | None
    as_of: DateTimeMin
    daily: list[DailyValueOut]
