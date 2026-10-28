"""动作变更单写回；事件复用同一套行校验和只追加版本收尾。"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.action.models import (
    ActionParam,
    ActionType,
    CriterionKind,
    EditOp,
    EffectKind,
    OntologyEditRule,
    SideEffect,
    SubmissionCriterion,
    TypeStatus,
)
from app.modules.admin.models import SysUser
from app.modules.integration.models import ExtSystem
from app.modules.integration.service import is_writeback_target
from app.modules.static.models import ObjectType
from app.modules.static.writeback import Ctx, owner_name, register_writeback, writeback_error

PENDING = "action_event_writeback"


@event.listens_for(Session, "after_soft_rollback")
def _discard_pending(session: Session, transaction: Any) -> None:
    session.info.pop(PENDING, None)


def text(value: Any, field: str, limit: int | None = None, *, required: bool = False) -> str:
    if (
        not isinstance(value, str)
        or "\0" in value
        or (limit is not None and len(value) > limit)
        or (required and not value.strip())
    ):
        raise writeback_error(f"「{field}」须为有效字符串", field=field)
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise writeback_error(f"「{field}」包含无效 Unicode 字符", field=field) from None
    return value


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise writeback_error(f"「{field}」须为布尔值", field=field)
    return value


def enum_value(enum: Any, value: Any, field: str) -> Any:
    text(value, field)
    try:
        return enum(value)
    except ValueError:
        raise writeback_error(f"「{field}」取值不认识", field=field) from None


def string(limit: int | None = None, *, required: bool = False) -> Callable:
    return lambda value, field: text(value, field, limit, required=required)


def choice(enum: Any) -> Callable:
    return lambda value, field: enum_value(enum, value, field)


def optional_string(limit: int) -> Callable:
    return lambda value, field: None if value is None else text(value, field, limit)


def touched(ctx: Ctx, kind: str, tid: str) -> None:
    pending = ctx.session.info.setdefault(PENDING, {}).setdefault(kind, {})
    pending.setdefault(tid, {})[ctx.cr.code] = (ctx.ver, ctx.now, ctx.by, ctx.cr.title)


async def finish_versions(
    session: Any, kind: str, types: list[Any], cr_ids: list[str], history: Any, fk: str, definition: Callable
) -> None:
    """所有段已写回后，一个目标只追加一个完整定义；回滚后从历史最大 vN 继续。"""
    pending = session.info.get(PENDING, {}).pop(kind, {})
    created = session.info.get(PENDING, {}).get("created", set())
    created.difference_update((kind, tid) for tid in pending)
    for obj in types:
        changes = [v for code, v in pending.get(obj.code, {}).items() if code in cr_ids]
        if not changes:
            continue
        versions = list(await session.scalars(select(history.ver).where(getattr(history, fk) == obj.id)))
        numbers = [int(v[1:]) for v in [obj.version, *versions] if v.startswith("v") and v[1:].isdigit()]
        obj.version = f"v{max(numbers, default=0) + 1}"
        text(obj.version, "version", 16)
        obj.status, obj.retired_release_id = TypeStatus.published, None
        ver, now, by, _ = changes[-1]
        by_name = await session.scalar(select(SysUser.name).where(SysUser.code == by)) or by
        session.add(
            history(
                **{
                    fk: obj.id,
                    "ver": obj.version,
                    "release_ver": ver,
                    "at": now,
                    "by_name": by_name,
                    "change_note": "；".join(v[3] for v in changes),
                    "definition": definition(obj),
                }
            )
        )


async def edit_rows(
    ctx: Ctx,
    parent: Any,
    rows: list[Any],
    model: Any,
    fk: str,
    fields: dict[str, tuple[str, Callable]],
    identity: Callable,
    required: set[str],
    *,
    ast: tuple[str, str] | None = None,
    forbidden: set[str] | None = None,
    ignored: set[str] | None = None,
) -> set[int]:
    """按旧 key 定位唯一行，支持 identity 改名；不重建子表以免丢掉内部列。"""
    existing = list(await ctx.session.scalars(select(model).where(getattr(model, fk) == parent.id)))
    changed: set[int] = set()
    for item in rows:
        key = text(item.item_key, "key", 128, required=True)
        matches = [row for row in existing if identity(row) == key]
        if len(matches) > 1:
            raise writeback_error("行 key 有歧义，不能写回", key=key)
        if item.op not in {"add", "update", "delete"}:
            raise writeback_error("未知行操作", key=key)
        if item.op == "add" and matches:
            raise writeback_error("行 key 已存在", key=key)
        if item.op != "add" and not matches:
            raise writeback_error("原行不存在", key=key)
        if item.op == "delete":
            changed.discard(matches[0].id)
            await ctx.session.delete(matches[0])
            existing.remove(matches[0])
            await ctx.session.flush()
            continue
        data = item.after
        if not isinstance(data, dict) or set(data) - fields.keys() - (ignored or set()):
            raise writeback_error("行数据须为受支持的字段对象", key=key)
        if item.op == "add" and not required <= data.keys():
            raise writeback_error("新增行缺少必填字段", key=key)
        values = {fields[k][0]: fields[k][1](v, k) for k, v in data.items() if k in fields}
        row = (
            matches[0]
            if matches
            else model(**{fk: parent.id, "seq": max((r.seq for r in existing), default=0) + 1})
        )
        # 先构造最终 identity；不会把 update(旧 key, 新 identity) 误当成新增。
        candidate = SimpleNamespace(
            **{col: values.get(col, getattr(row, col, None)) for col, _ in fields.values()}
        )
        new_key = identity(candidate)
        text(new_key, "key", 128, required=True)  # 后续仍须能作为 CrItem.item_key 编辑。
        if (item.op == "add" and key != new_key) or new_key in (forbidden or set()):
            raise writeback_error("行 key 与字段不匹配或与保留字段冲突", key=key)
        if any(r is not row and identity(r) == new_key for r in existing):
            raise writeback_error("改名后的行 key 已存在", key=new_key)
        if ast and ast[0] in values and getattr(row, ast[0], None) != values[ast[0]]:
            setattr(row, ast[1], None)
        for col, value in values.items():
            setattr(row, col, value)
        if not matches:
            existing.append(row)
            ctx.session.add(row)
        await ctx.session.flush()
        changed.add(row.id)
    return changed


PARAMS = {
    "name": ("name", string(64, required=True)),
    "api": ("api", string(64, required=True)),
    "type": ("type", string(64, required=True)),
    "required": ("required", boolean),
    "def": ("default_text", string()),
    "rule": ("rule_text", string()),
}
CONDS = {
    "kind": ("kind", choice(CriterionKind)),
    "name": ("name", string(64, required=True)),
    "expr": ("expr_text", string()),
    "fail": ("fail_msg", string()),
}
EDITS = {
    "op": ("op", choice(EditOp)),
    "obj": ("obj", string(128, required=True)),
    "field": ("field", string()),
}
EFFECTS = {
    "kind": ("kind", choice(EffectKind)),
    "target": ("target", string(255, required=True)),
    "when": ("when_text", string()),
    "note": ("note", string()),
}


async def _action(ctx: Ctx, tid: str) -> ActionType:
    text(tid, "id", 64, required=True)
    at = await ctx.session.scalar(select(ActionType).where(ActionType.code == tid))
    if at is None:
        if not ctx.is_new:
            raise writeback_error("动作类型不存在", id=tid)
        name = text(ctx.name or tid, "name", 64, required=True)
        if await ctx.session.scalar(select(ActionType.id).where(ActionType.api == tid)):
            raise writeback_error("动作 API 名已存在", id=tid)
        from app.modules.static.models import CrTarget

        creator = await ctx.session.scalar(select(CrTarget.created_by).where(
            CrTarget.cr_id == ctx.cr.id, CrTarget.target_kind == "action", CrTarget.target_id == tid, CrTarget.is_new.is_(True)
        ))
        if not creator:
            raise writeback_error("新动作类型缺少创建者记录", id=tid)
        at = ActionType(code=tid, api=tid, name=name, grp="", status=TypeStatus.published, version="v0", created_by=creator)
        ctx.session.add(at)
        await ctx.session.flush()
        ctx.session.info.setdefault(PENDING, {}).setdefault("created", set()).add(("action", tid))
    elif at.retired_release_id is not None:
        at.retired_release_id = None
    elif ctx.is_new and ("action", tid) not in ctx.session.info.get(PENDING, {}).get("created", set()):
        raise writeback_error("新动作类型编码已存在", id=tid)
    touched(ctx, "action", tid)
    return at


async def params(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    from app.modules.integration.action_edits import mapping_json, validate_source_definition

    for item in rows:
        raw = item.after.get("def") if item.op != "delete" and isinstance(item.after, dict) else None
        if isinstance(raw, str) and raw.lstrip().startswith("{"):
            spec = mapping_json(raw)
            if "function" in spec:
                await validate_source_definition(ctx.session, spec)
    at = await _action(ctx, tid)
    await edit_rows(
        ctx,
        at,
        rows,
        ActionParam,
        "action_type_id",
        PARAMS,
        lambda r: r.api,
        {"name", "api", "type", "required"},
        ast=("rule_text", "rule_expr"),
    )
    return set()


async def conds(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    at = await _action(ctx, tid)
    await edit_rows(
        ctx,
        at,
        rows,
        SubmissionCriterion,
        "action_type_id",
        CONDS,
        lambda r: r.name,
        {"kind", "name", "expr", "fail"},
        ast=("expr_text", "expr_ast"),
    )
    return set()


def writeback_mapping(value: Any) -> dict:
    from app.modules.integration.action_edits import mapping_json

    try:
        return mapping_json(value)
    except AppError as exc:
        # This parser only checks caller-supplied JSON; runtime binding failures remain untouched.
        raise writeback_error(exc.message) from None


async def validate_edit(session, op, obj_name, field):
    from app.modules.integration.action_edits import (
        WRITABLE,
        column_value,
        object_binding,
        relation_binding,
        validate_source_definition,
    )

    spec = writeback_mapping(field)
    if op == "关联":
        await relation_binding(session, obj_name)
        if set(spec) != {"from", "to"}:
            raise writeback_error("关联须指定两端对象引用")
    else:
        obj, governed = await object_binding(session, obj_name)
        if governed.write_columns is not None and op != "修改":
            raise writeback_error("配置绑定仅允许修改已授权字段")
        writable = governed.write_columns if governed.write_columns is not None else WRITABLE.get(obj.code, set())
        values = spec.get("values", spec)
        if not isinstance(values, dict) or not 1 <= len(values) <= 20:
            raise writeback_error("属性赋值须为明确字段映射")
        if "values" in spec and set(spec) - {"target", "values", "lockVersion"}:
            raise writeback_error("编辑映射含未知字段")
        for api, source in values.items():
            props = [p for p in obj.properties if p.api_name == api]
            column = (props[0].backing_column or props[0].source_column) if len(props) == 1 else None
            if (
                len(props) != 1
                or column not in writable
                or props[0].is_pk
                or props[0].api_name == obj.pk_property
                or props[0].is_sensitive
                or props[0].visibility == "hidden"
                or props[0].transform
            ):
                raise writeback_error("属性不在当前已发布可写映射中", field=api)
            await validate_source_definition(session, source)
            if set(source) == {"value"}:
                column_value(governed.table.c[column], source["value"])


def declaration(value):
    from app.modules.integration.action_edits import declared_text

    try:
        return declared_text(value)
    except AppError as exc:
        raise writeback_error(exc.message) from None


def final_edits(ctx, at, ids=None):
    if getattr(ctx, "final_checks", None) is None:
        raise writeback_error("动作发布缺少最终校验上下文")

    async def check():
        from app.common.ontology import require_ontology_id
        from app.modules.integration.action_edits import WEBHOOK_CATEGORY

        if ctx.cr.code not in ctx.session.info.get(PENDING, {}).get("action", {}).get(at.code, {}):
            raise writeback_error("动作版本上下文已失效", id=at.code)
        statement = select(OntologyEditRule).where(OntologyEditRule.action_type_id == at.id)
        if ids is not None:
            statement = statement.where(OntologyEditRule.id.in_(ids))
        edits = list(await ctx.session.scalars(statement.execution_options(populate_existing=True)))
        for edit in edits:
            if declaration(edit.field):
                system = await ctx.session.scalar(
                    select(ExtSystem)
                    .where(
                        ExtSystem.id == at.sys_id, ExtSystem.ontology_id == require_ontology_id(ctx.session)
                    )
                    .with_for_update(read=True)
                    .execution_options(populate_existing=True)
                )
                if not system or system.category != WEBHOOK_CATEGORY or not is_writeback_target(system):
                    raise writeback_error("自然语言声明仅允许有效的 Webhook 外部系统")
            elif ids is not None:
                await validate_edit(ctx.session, edit.op.value, edit.obj, edit.field)

    ctx.final_checks.append(check)


async def edits(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    for item in rows:
        if item.op == "delete" or not isinstance(item.after, dict) or "field" not in item.after:
            continue
        if not declaration(item.after["field"]) and "op" in item.after and "obj" in item.after:
            await validate_edit(ctx.session, item.after["op"], item.after["obj"], item.after["field"])
    at = await _action(ctx, tid)
    changed = await edit_rows(
        ctx,
        at,
        rows,
        OntologyEditRule,
        "action_type_id",
        EDITS,
        lambda r: f"{r.op.value if isinstance(r.op, EditOp) else r.op}:{r.obj}",
        {"op", "obj", "field"},
    )
    final_edits(ctx, at, changed)
    return set()


async def effects(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    from app.modules.integration.action_edits import validate_source_definition
    from app.modules.integration.action_effects import STANDARD, effect_timing

    for item in rows:
        if item.op == "delete" or not isinstance(item.after, dict):
            continue
        value = item.after
        text(value.get("kind"), "kind", 16, required=True)
        text(value.get("target"), "target", 255, required=True)
        if value.get("kind") == "事件" and value.get("target", "").split(" ", 1)[0] in STANDARD:
            continue
        try:
            effect_timing(value)
        except AppError as exc:
            raise writeback_error(exc.message) from None
        spec = writeback_mapping(value.get("note"))
        if not isinstance(spec.get("payload"), dict):
            raise writeback_error("副作用须指定载荷映射")
        if value.get("kind") == "事件":
            from app.modules.event.models import EventType

            event_type = await ctx.session.scalar(
                select(EventType).where(
                    EventType.code == value.get("target"),
                    EventType.status == TypeStatus.published,
                    EventType.retired_release_id.is_(None),
                )
            )
            if (
                set(spec) != {"version", "object", "payload"}
                or event_type is None
                or event_type.version != spec["version"]
            ):
                raise writeback_error("自定义事件必须绑定可用的固定发布版本")
            fields = {p.api: p for p in event_type.payload}
            if set(spec["payload"]) - fields.keys() or any(
                p.required and p.api not in spec["payload"] for p in fields.values()
            ):
                raise writeback_error("自定义事件载荷与固定发布定义不一致")
        elif value.get("kind") not in ("通知", "Webhook") or set(spec) != {"payload"}:
            raise writeback_error("外部副作用类型或载荷映射无效")
        for source in spec["payload"].values():
            await validate_source_definition(ctx.session, source)
    at = await _action(ctx, tid)
    await edit_rows(
        ctx,
        at,
        rows,
        SideEffect,
        "action_type_id",
        EFFECTS,
        lambda r: r.target,
        {"kind", "target", "when", "note"},
    )
    return set()


async def config(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    at = await _action(ctx, tid)
    previous_system = at.sys_id
    for item in rows:
        if item.item_key != "cfg" or item.op not in {"add", "update", "delete"}:
            raise writeback_error("写回配置须使用 cfg key")
        data = {"sysId": None, "endpoint": "—", "idemKey": "", "httpMapping": None} if item.op == "delete" else item.after
        if not isinstance(data, dict) or set(data) - {"sysId", "endpoint", "idemKey", "httpMapping"}:
            raise writeback_error("写回配置字段不合法")
        if "sysId" in data:
            code = optional_string(64)(data["sysId"], "sysId")
            system = (
                await ctx.session.scalar(select(ExtSystem).where(ExtSystem.code == code)) if code else None
            )
            if code and system is None:
                raise writeback_error("外部系统不存在", sysId=code)
            if system is not None and not is_writeback_target(system):
                raise writeback_error("外部写回系统须为生产环境且未停用", sysId=code)
            at.sys_id = system.id if system else None
            if system is None:
                at.http_mapping = None
        if "endpoint" in data:
            at.endpoint = text(data["endpoint"], "endpoint", 255)
        if "idemKey" in data:
            at.biz_key_template = text(data["idemKey"], "idemKey", 255)
        if "httpMapping" in data:
            from app.modules.integration.http_mapping import validate_mapping
            params = await ctx.session.scalars(select(ActionParam.api).where(ActionParam.action_type_id == at.id))
            validate_mapping(data["httpMapping"], set(params))
            if data["httpMapping"] is not None and at.sys_id is None:
                raise writeback_error("HTTP 映射须绑定外部系统")
            at.http_mapping = data["httpMapping"]
    await ctx.session.flush()
    if at.sys_id != previous_system:
        final_edits(ctx, at)
    return set()


async def definition(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    at = await _action(ctx, tid)
    fields = {
        "name": ("name", string(64, required=True)),
        "api": ("api", string(64, required=True)),
        "group": ("grp", string(32)),
        "targetKind": ("target_kind", optional_string(64)),
        "owner": ("owner", string(64)),
        "desc": ("desc_tpl", string()),
        "runners": ("runners", string()),
        "approval": ("approval", string()),
    }
    for item in rows:
        if item.item_key not in fields or item.op not in {"add", "update"}:
            raise writeback_error("动作基本信息字段或操作不合法", field=item.item_key)
        column, validate = fields[item.item_key]
        value = validate(item.after, item.item_key)
        if item.item_key == "owner":
            value = await owner_name(ctx, value)
        if item.item_key == "api" and await ctx.session.scalar(
            select(ActionType.id).where(ActionType.api == value, ActionType.id != at.id)
        ):
            raise writeback_error("动作 API 名已存在", api=value)
        setattr(at, column, value)
    if at.target_kind:
        obj = await ctx.session.scalar(select(ObjectType).where(ObjectType.code == at.target_kind))
        if obj is None or obj.status.value != "已发布":
            raise writeback_error("动作关联对象须为已发布对象类型", targetKind=at.target_kind)
        at.target, at.target_api = obj.display_name, obj.api_name
    else:
        at.target, at.target_api = "", ""
    await ctx.session.flush()
    return set()


async def policy(ctx: Ctx, tid: str, rows: list[Any]) -> set[str]:
    """执行策略段：key 固定 policy；删除 = 回到默认（人工确认）。发布时并进该版完整定义。"""
    from app.modules.action.policy import parse

    at = await _action(ctx, tid)
    for item in rows:
        if item.item_key != "policy" or item.op not in {"add", "update", "delete"}:
            raise writeback_error("执行策略须使用 policy key", field="policy")
        try:
            value = None if item.op == "delete" else parse(item.after).dump()
        except ValueError as exc:
            raise writeback_error(f"执行策略无效：{exc}", field="policy") from None
        ctx.session.info.setdefault(PENDING, {}).setdefault("policy", {})[at.code] = value
    return set()


def register() -> None:
    for section, hook in {
        "def": definition,
        "params": params,
        "conds": conds,
        "edits": edits,
        "effects": effects,
        "config": config,
        "policy": policy,
    }.items():
        register_writeback("action", section, hook)
