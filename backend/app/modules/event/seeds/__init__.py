"""event 模块 seed（读 JSON 组 action：export-mock 把事件类型和动作类型一起导出在 action/ 下）。

- EVENT_TYPES.json → event_type：code = id；retention "N 天" → retention_days；ruleId → rule_code（软引用）。
- EVENT_TYPES.json · specific → event_payload_field：键 (event_type_id, api)。
- derived/payloadOf.json → event_envelope_field：payloadOf = specific + 10 个通用信封字段，
  去掉 specific 后的部分，全局 10 行，键 api。
- derived/subscribersOf.json → event_subscription：键 (event_type_id, name)。
- derived/versionsOf.event.json → event_type_version：第 i 次调用 = EVENT_TYPES[i]（同动作）。
- derived/eventsOf.json → event（palantir-r2）：code = id（EV-…），at 按北京时间解析，typeId → event_type.id。
- derived/eventDaily.json → event_daily_stat（palantir-r2）：调用参数 (typeId, weekly)，结果 14 天
  {date:"MM-DD", value}；年份取 clock.today() 的年，月日在今天之后则算去年 [推断]。
"""

import re

from app.core.enums import from_api
from app.core.schema import parse_display_dt
from app.modules.action.models import TypeStatus
from app.modules.action.seeds import mmdd_to_date, versions_rows
from app.modules.event.models import (
    Event,
    EventDailyStat,
    EventEnvelopeField,
    EventOutcome,
    EventPayloadField,
    EventSource,
    EventSubscription,
    EventType,
    EventTypeVersion,
    SubscriberKind,
    SubscriberStatus,
)
from app.seeds import ARGS_KEY, Ref, Row, SeedContext, SeedSpec, derived_args

JSON_MODULE = "action"

PARENT = {"event_type_id": Ref(EventType, "code", nullable=False)}


def retention_days(text: str) -> int:
    m = re.fullmatch(r"\s*(\d+)\s*天\s*", text)
    if not m:
        raise ValueError(f"retention 不是「N 天」：{text!r}")
    return int(m.group(1))


def _type(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "api": r["api"],
        "source": from_api(EventSource, r["source"]).name,
        "source_ref": r["sourceRef"],
        "rule_code": r.get("ruleId"),
        "object": r["object"],
        "object_api": r["objectApi"],
        "obj_kind": r.get("objKind"),
        "status": from_api(TypeStatus, r["status"]).name,
        "version": r["version"],
        "owner": r["owner"],
        "description": r["desc"],
        "dedup_key_rule": r["dedupKey"],
        "dedup_window": r["dedupWindow"],
        "delivery": r["delivery"],
        "ordering": r["ordering"],
        "retention_days": retention_days(r["retention"]),
        "weekly": r.get("weekly") or 0,
    }


def _field(f: Row, seq: int) -> Row:
    return {
        "seq": seq,
        "name": f["name"],
        "api": f["api"],
        "type": f["type"],
        "required": f["required"],
        "description": f["desc"],
        "sample": f["sample"],
    }


def _payload(r: Row, _ctx: SeedContext) -> list[Row]:
    return [{"event_type_id": r["id"], **_field(f, i)} for i, f in enumerate(r.get("specific") or [], 1)]


def _envelope(r: Row, ctx: SeedContext) -> Row | None:
    et = next(t for t in ctx.load("EVENT_TYPES.json") if t["id"] == derived_args(r)[0])
    if any(f["api"] == r["api"] for f in et["specific"]):
        return None
    call = next(c for c in ctx.load("derived/payloadOf.json") if c["args"][0]["id"] == et["id"])
    item = {k: v for k, v in r.items() if k != ARGS_KEY}
    return _field(r, call["result"].index(item) - len(et["specific"]) + 1)


def _subscriber(r: Row, ctx: SeedContext) -> Row:
    tid = derived_args(r)[0]
    call = next(c for c in ctx.load("derived/subscribersOf.json") if c["args"][0]["id"] == tid)
    item = {k: v for k, v in r.items() if k != ARGS_KEY}
    return {
        "event_type_id": tid,
        "seq": call["result"].index(item) + 1,
        "kind": from_api(SubscriberKind, r["kind"]).name,
        "name": r["name"],
        "rule_code": r.get("ruleId"),
        "filter_text": r["filter"],
        "then_text": r["then"],
        "status": from_api(SubscriberStatus, r["status"]).name,
        "weekly": r.get("weekly") or 0,
    }


def _versions(r: Row, ctx: SeedContext) -> list[Row]:
    return versions_rows(
        r,
        ctx,
        types_file="EVENT_TYPES.json",
        versions_file="derived/versionsOf.event.json",
        fk="event_type_id",
    )


def _event(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "event_type_id": r["typeId"],
        "occurred_at": parse_display_dt(r["at"]),
        "obj_kind": r.get("objKind"),
        "object_id": r["objId"],
        "obj_label": r["objLabel"],
        "summary": r["summary"],
        "outcome": from_api(EventOutcome, r["outcome"]).name,
        "subscriber_count": r["subscribers"],
        "dedup_key": r["dedupKey"],
        "latency_ms": r["latencyMs"],
    }


def _daily(r: Row, _ctx: SeedContext) -> Row:
    return {"event_type_id": derived_args(r)[0], "stat_date": mmdd_to_date(r["date"]), "value": r["value"]}


SEEDS: list[SeedSpec] = [
    SeedSpec(EventType, "EVENT_TYPES.json", key=("code",), transform=_type, description="事件类型"),
    SeedSpec(
        EventPayloadField, "EVENT_TYPES.json", key=("event_type_id", "api"), transform=_payload, refs=PARENT
    ),
    SeedSpec(EventEnvelopeField, "derived/payloadOf.json", key=("api",), transform=_envelope),
    SeedSpec(
        EventSubscription,
        "derived/subscribersOf.json",
        key=("event_type_id", "name"),
        transform=_subscriber,
        refs=PARENT,
    ),
    SeedSpec(
        EventTypeVersion,
        "EVENT_TYPES.json",
        key=("event_type_id", "ver"),
        transform=_versions,
        description="事件类型版本（versionsOf）",
        refs=PARENT,
    ),
    SeedSpec(
        Event,
        "derived/eventsOf.json",
        key=("code",),
        transform=_event,
        description="事件（eventsOf，近期事件）",
        refs=PARENT,
    ),
    SeedSpec(
        EventDailyStat,
        "derived/eventDaily.json",
        key=("event_type_id", "stat_date"),
        transform=_daily,
        description="事件类型近 14 天趋势（eventDaily）",
        refs=PARENT,
    ),
]
