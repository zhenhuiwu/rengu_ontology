"""事件载荷和订阅方的发布写回。通用信封和投递统计不由草稿修改。"""

from typing import Any

from sqlalchemy import select

from app.modules.action.models import TypeStatus
from app.modules.action.writeback import (
    PENDING,
    boolean,
    choice,
    edit_rows,
    optional_string,
    string,
    text,
    touched,
)
from app.modules.event.models import (
    EventEnvelopeField,
    EventPayloadField,
    EventSource,
    EventSubscription,
    EventType,
    SubscriberKind,
    SubscriberStatus,
)
from app.modules.static.models import ObjectType
from app.modules.static.writeback import Ctx, owner_name, register_writeback, writeback_error

PAYLOAD = {
    "name": ("name", string(64, required=True)),
    "api": ("api", string(64, required=True)),
    "type": ("type", string(64, required=True)),
    "required": ("required", boolean),
    "desc": ("description", string()),
    "sample": ("sample", string()),
}
SUBS = {
    "kind": ("kind", choice(SubscriberKind)),
    "name": ("name", string(128, required=True)),
    "ruleId": ("rule_code", optional_string(32)),
    "filter": ("filter_text", string()),
    "then": ("then_text", string()),
    "status": ("status", choice(SubscriberStatus)),
}


async def _event(ctx: Ctx, tid: str) -> EventType:
    text(tid, "id", 64, required=True)
    et = await ctx.session.scalar(select(EventType).where(EventType.code == tid))
    if et is None:
        if not ctx.is_new:
            raise writeback_error("事件类型不存在", id=tid)
        name = text(ctx.name or tid, "name", 64, required=True)
        if await ctx.session.scalar(select(EventType.id).where(EventType.api == tid)):
            raise writeback_error("事件 API 名已存在", id=tid)
        et = EventType(
            code=tid,
            api=tid,
            name=name,
            source=EventSource.external,
            status=TypeStatus.published,
            version="v0",
            retention_days=30,
        )
        ctx.session.add(et)
        await ctx.session.flush()
        ctx.session.info.setdefault(PENDING, {}).setdefault("created", set()).add(("event", tid))
    elif et.retired_release_id is not None:
        et.retired_release_id = None
    elif ctx.is_new and ("event", tid) not in ctx.session.info.get(PENDING, {}).get("created", set()):
        raise writeback_error("新事件类型编码已存在", id=tid)
    touched(ctx, "event", tid)
    return et


async def payload(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    from app.modules.event.platform_context import validate_draft

    await validate_draft(ctx.session, tid, ctx.cr.id, complete=True)
    et = await _event(ctx, tid)
    envelope = set(await ctx.session.scalars(select(EventEnvelopeField.api)))
    await edit_rows(
        ctx,
        et,
        rows,
        EventPayloadField,
        "event_type_id",
        PAYLOAD,
        lambda r: r.api,
        {"name", "api", "type", "required"},
        forbidden=envelope,
    )
    return set()


async def subs(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    et = await _event(ctx, tid)
    await edit_rows(
        ctx,
        et,
        rows,
        EventSubscription,
        "event_type_id",
        SUBS,
        lambda r: r.name,
        {"kind", "name", "filter", "then", "status"},
        ast=("filter_text", "filter_expr"),
        ignored={"weekly"},
    )
    return set()


async def definition(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    from app.modules.event.platform_context import ACTION_RUN, validate_draft

    platform = await validate_draft(ctx.session, tid, ctx.cr.id, complete=True)
    et = await _event(ctx, tid)
    fields = {
        "name": ("name", string(64, required=True)),
        "api": ("api", string(64, required=True)),
        "source": ("source", choice(EventSource)),
        "sourceRef": ("source_ref", string()),
        "ruleId": ("rule_code", optional_string(32)),
        "objKind": ("obj_kind", optional_string(64)),
        "owner": ("owner", string(64)),
        "desc": ("description", string()),
        "dedupKey": ("dedup_key_rule", string()),
        "dedupWindow": ("dedup_window", string(64)),
        "delivery": ("delivery", string(32)),
        "ordering": ("ordering", string(64)),
    }
    for item in rows:
        if item.op not in {"add", "update"}:
            raise writeback_error("事件基本信息操作不合法", field=item.item_key)
        if item.item_key == "retention":
            value = text(item.after, "retention", 16).strip()
            days = value.removesuffix("天").strip()
            if (
                not value.endswith("天")
                or not days.isascii()
                or not days.isdigit()
                or not 1 <= int(days) <= 3650
            ):
                raise writeback_error("保留时长须为 1 至 3650 天", field="retention")
            et.retention_days = int(days)
            continue
        if item.item_key not in fields:
            raise writeback_error("事件基本信息字段不合法", field=item.item_key)
        column, validate = fields[item.item_key]
        value = validate(item.after, item.item_key)
        if item.item_key == "owner":
            value = await owner_name(ctx, value)
        if item.item_key == "api" and await ctx.session.scalar(
            select(EventType.id).where(EventType.api == value, EventType.id != et.id)
        ):
            raise writeback_error("事件 API 名已存在", api=value)
        setattr(et, column, value)
    if platform:
        if et.obj_kind not in (None, ACTION_RUN):
            raise writeback_error("标准动作完成事件不能关联业务对象", field="objKind")
        et.obj_kind, et.object, et.object_api = None, "动作执行记录", "ActionRun"
    elif et.obj_kind:
        obj = await ctx.session.scalar(select(ObjectType).where(ObjectType.code == et.obj_kind))
        if obj is None or obj.status.value != "已发布":
            raise writeback_error("事件关联对象须为已发布对象类型", objKind=et.obj_kind)
        et.object, et.object_api = obj.display_name, obj.api_name
    else:
        et.object, et.object_api = "", ""
    if et.rule_code:
        from app.modules.dynamic.rules_models import Rule

        if await ctx.session.scalar(select(Rule.id).where(Rule.code == et.rule_code)) is None:
            raise writeback_error("事件来源规则不存在", ruleId=et.rule_code)
    if et.source == EventSource.rule and not et.rule_code:
        raise writeback_error("规则来源事件须选择真实规则", field="ruleId")
    await ctx.session.flush()
    return set()


def register() -> None:
    register_writeback("event", "def", definition)
    register_writeback("event", "payload", payload)
    register_writeback("event", "subs", subs)
