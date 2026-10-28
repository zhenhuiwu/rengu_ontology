"""发布快照 `eventTypes` 段（action-event-api §4.2）。

形状：`{<事件 id>: {id,name,api,source,sourceRef,ruleId,object,objectApi,objKind,status,version,owner,desc,
dedupKey,dedupWindow,delivery,ordering,retention, payload[]（只含 specific，信封字段全局共用不进快照）,
subscribers[]（不含 weekly 统计）}}`。登记 / seed / restore 规则同 action.sections；
restore 时订阅方 weekly 按 name 沿用原值，信封字段全局共用不动。
"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import from_api
from app.modules.action.models import TypeStatus
from app.modules.action.sections import retire_missing
from app.modules.event.models import (
    EventPayloadField,
    EventSource,
    EventSubscription,
    EventType,
    EventTypeVersion,
    SubscriberKind,
    SubscriberStatus,
)
from app.modules.event.seeds import retention_days

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
        "schema": getattr(f, "schema", None),
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
    return list((await session.scalars(stmt.execution_options(populate_existing=True))).all())


async def restore_event_types(session: AsyncSession, content: dict[str, Any], release_id: int) -> None:
    existing = {et.code: et for et in (await session.scalars(select(EventType))).all()}
    weekly = {
        (s.event_type_id, s.name): s.weekly for s in (await session.scalars(select(EventSubscription))).all()
    }
    for code, d in content.items():
        et = existing.get(code) or EventType(code=code)
        et.name, et.api = d["name"], d["api"]
        et.source, et.source_ref, et.rule_code = (
            from_api(EventSource, d["source"]),
            d.get("sourceRef") or "",
            d.get("ruleId"),
        )
        et.object, et.object_api, et.obj_kind = (
            d.get("object") or "",
            d.get("objectApi") or "",
            d.get("objKind"),
        )
        et.status, et.version = from_api(TypeStatus, d["status"]), d["version"]
        et.owner, et.description = d.get("owner") or "", d.get("desc") or ""
        et.dedup_key_rule, et.dedup_window = d.get("dedupKey") or "", d.get("dedupWindow") or ""
        et.delivery, et.ordering = d.get("delivery") or "", d.get("ordering") or ""
        et.retention_days = retention_days(d["retention"])
        et.retired_release_id = None
        session.add(et)
        await session.flush()
        for m in (EventPayloadField, EventSubscription):
            await session.execute(delete(m).where(m.event_type_id == et.id))
        seed = et.is_seed
        session.add_all(
            [
                EventPayloadField(
                    event_type_id=et.id, seq=i, name=f["name"], api=f["api"], type=f["type"],
                    required=bool(f.get("required")), description=f.get("desc") or "",
                    sample=f.get("sample") or "", schema=f.get("schema"), is_seed=seed,
                )
                for i, f in enumerate(d.get("payload") or [], 1)
            ]
            + [
                EventSubscription(
                    event_type_id=et.id, seq=i, kind=from_api(SubscriberKind, s["kind"]), name=s["name"],
                    rule_code=s.get("ruleId"), filter_text=s.get("filter") or "",
                    then_text=s.get("then") or "",
                    status=from_api(SubscriberStatus, s["status"]), weekly=weekly.get((et.id, s["name"]), 0),
                    is_seed=seed,
                )
                for i, s in enumerate(d.get("subscribers") or [], 1)
            ]
        )  # fmt: skip
        await session.flush()
        await session.refresh(et, ["payload", "subscribers"])
    await retire_missing(session, EventType, set(content), release_id)


class EventTypesSection:
    section = SECTION

    async def build_section(
        self, session: AsyncSession, base: Any | None, change_order_ids: list[str]
    ) -> Any:
        from app.modules.action.writeback import finish_versions

        types = await current_types(session)
        await finish_versions(
            session, "event", types, change_order_ids, EventTypeVersion, "event_type_id", event_def
        )
        return {et.code: event_def(et) for et in types}

    def seed_section(self, ver: str, ctx: Any) -> Any:
        return {t["id"]: event_def_from_json(t, ctx) for t in ctx.load("EVENT_TYPES.json", group=GROUP)}

    async def restore_section(self, session: AsyncSession, content: Any, *, release_id: int) -> None:
        if content is None:  # 那一版快照里没有这一节：什么都不做 [推断]
            return
        await restore_event_types(session, content, release_id)


BUILDER = EventTypesSection()


def register() -> EventTypesSection:
    """幂等：已登记（本实例或测试替身）就不重复登记。由骨架 load_sections() 调用，import 时不登记。"""
    from app.modules.event.writeback import register as register_writebacks
    from app.modules.versioning.sections import register_section, registered_sections

    register_writebacks()
    if SECTION not in registered_sections():
        register_section(BUILDER)
    return BUILDER
