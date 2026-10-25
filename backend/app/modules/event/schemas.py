"""对外 Schema（字段名 = 前端 mock：EVENT_TYPES / payloadOf / subscribersOf / versionsOf）。"""

from app.core.schema import ApiModel
from app.modules.action.models import TypeStatus
from app.modules.event.models import EventSource, SubscriberKind, SubscriberStatus


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
