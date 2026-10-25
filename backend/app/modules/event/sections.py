"""发布快照 `eventTypes` 段（action-event-api §4.2）。

形状：`{<事件 id>: {id,name,api,source,sourceRef,ruleId,object,objectApi,objKind,status,version,owner,desc,
dedupKey,dedupWindow,delivery,ordering,retention, payload[]（只含 specific，信封字段全局共用不进快照）,
subscribers[]（不含 weekly 统计）}}`。登记 / seed 规则同 action.sections。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.event.models import EventType

SECTION = "eventTypes"
GROUP = "action"  # export-mock 把事件也导出在 action/ 下

FIELD_KEYS = ("name", "api", "type", "required", "desc", "sample")


def field_dict(f: Any) -> dict[str, Any]:
    return {
        "name": f.name,
        "api": f.api,
        "type": f.type,
        "required": f.required,
        "desc": f.description,
        "sample": f.sample,
    }


def event_def(et: EventType) -> dict[str, Any]:
    return {
        "id": et.code,
        "name": et.name,
        "api": et.api,
        "source": et.source.value,
        "sourceRef": et.source_ref,
        "ruleId": et.rule_code,
        "object": et.object,
        "objectApi": et.object_api,
        "objKind": et.obj_kind,
        "status": et.status.value,
        "version": et.version,
        "owner": et.owner,
        "desc": et.description,
        "dedupKey": et.dedup_key_rule,
        "dedupWindow": et.dedup_window,
        "delivery": et.delivery,
        "ordering": et.ordering,
        "retention": f"{et.retention_days} 天",
        "payload": [field_dict(f) for f in et.payload],
        "subscribers": [
            {
                "kind": s.kind.value,
                "name": s.name,
                "ruleId": s.rule_code,
                "filter": s.filter_text,
                "then": s.then_text,
                "status": s.status.value,
            }
            for s in et.subscribers
        ],
    }


def event_def_from_json(t: dict[str, Any], ctx: Any) -> dict[str, Any]:
    subs: list[dict[str, Any]] = []
    for call in ctx.load("derived/subscribersOf.json", group=GROUP):
        if call["args"][0].get("id") == t["id"]:
            subs = call["result"]
    keys = (
        "id", "name", "api", "source", "sourceRef", "object", "objectApi", "objKind", "status", "version",
        "owner", "desc", "dedupKey", "dedupWindow", "delivery", "ordering", "retention",
    )  # fmt: skip
    return {
        **{k: t.get(k) for k in keys},
        "ruleId": t.get("ruleId"),
        "payload": [{k: f[k] for k in FIELD_KEYS} for f in t.get("specific") or []],
        "subscribers": [
            {
                "kind": s["kind"],
                "name": s["name"],
                "ruleId": s.get("ruleId"),
                "filter": s["filter"],
                "then": s["then"],
                "status": s["status"],
            }
            for s in subs
        ],
    }


async def current_types(session: AsyncSession) -> list[EventType]:
    stmt = select(EventType).where(EventType.retired_release_id.is_(None)).order_by(EventType.id)
    return list((await session.scalars(stmt)).all())


class EventTypesSection:
    section = SECTION

    async def build_section(
        self, session: AsyncSession, base: Any | None, change_order_ids: list[str]
    ) -> Any:
        return {et.code: event_def(et) for et in await current_types(session)}

    def seed_section(self, ver: str, ctx: Any) -> Any:
        return {t["id"]: event_def_from_json(t, ctx) for t in ctx.load("EVENT_TYPES.json", group=GROUP)}


def register() -> EventTypesSection:
    from app.modules.versioning.sections import register_section, registered_sections

    existing = registered_sections().get(SECTION)
    if isinstance(existing, EventTypesSection):
        return existing
    b = EventTypesSection()
    register_section(b)
    return b
