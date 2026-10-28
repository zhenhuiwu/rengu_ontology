"""Management API for explicit local table approvals."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated
from sqlalchemy import delete, select, text
from sqlalchemy.orm import undefer
from app.common.auth import CurrentUserDep, check_func
from app.common.sql_guard import _GOVERNED, _invalidate_bound, load_governed_tables
from app.core.errors import Forbidden, NotFound
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.data.governed_binding import inspect_table, invalid, validate_binding, validate_supplementary
from app.modules.data.models import GovernedBinding
from app.modules.static.models import ObjectType

router = APIRouter(tags=["governed-bindings"])


class BindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    read_columns: list[Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_]*$")]] = Field(alias="readColumns", max_length=100)
    write_columns: list[Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_]*$")]] = Field(alias="writeColumns", max_length=100)
    supplementary: dict | None = None
    binding_version: str | None = Field(default=None, alias="bindingVersion", max_length=32)


async def supplementary_available(session):
    return bool(await session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('public.governed_binding') AND attname='supplementary' AND NOT attisdropped)")))


async def binding_version(session, oid, code):
    return await session.scalar(text("SELECT xmin::text FROM governed_binding WHERE ontology_id=:oid AND object_code=:code"), {"oid": oid, "code": code})


def authorize(user):
    check_func(user, "connections", "manage")
    check_func(user, "object-types", "manage")
    if user.ontology_role != "owner":
        raise Forbidden("只有工作空间所有者可配置受控绑定")


async def object_for(session, user, code):
    obj = await session.scalar(select(ObjectType).where(ObjectType.ontology_id == user.ontology_id, ObjectType.code == code))
    if obj is None:
        raise NotFound("对象不存在")
    source = obj.source_table or ""
    name = source.removeprefix("public.")
    if not name or ('.' in source and not source.startswith('public.')):
        raise invalid("请先为对象配置 public 源表")
    return obj, name


@router.get("/governed-bindings/{code}")
async def detail(code: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    obj, name = await object_for(session, user, code)
    from app.modules.qa.object_source import primary_property
    primary = primary_property(obj)
    if primary is None:
        raise invalid("请先配置对象主键属性")
    await validate_binding(session, obj, name, [primary.backing_column or primary.source_column], [])
    table, can_update = await inspect_table(session, name)
    available = await supplementary_available(session)
    query = select(GovernedBinding).where(GovernedBinding.object_code == code, GovernedBinding.ontology_id == user.ontology_id)
    row = await session.scalar(query.options(undefer(GovernedBinding.supplementary)) if available else query)
    columns = []
    for p in obj.properties:
        column = p.backing_column or p.source_column
        if column in table.c and not p.is_sensitive and p.visibility != 'hidden' and not p.transform:
            columns.append({"column": column, "name": p.display_name, "api": p.api_name,
                            "writable": bool(can_update and not p.is_pk and p.api_name != obj.pk_property and column not in {'id', 'code', 'ontology_id', 'lock_version', 'updated_at', 'created_by'})})
    return {"objectId": code, "table": f"public.{name}", "columns": columns,
            "readColumns": row.read_columns if row and row.table_name == name else [],
            "writeColumns": row.write_columns if row and row.table_name == name else [], "bound": bool(row and row.table_name == name),
            "supplementary": row.supplementary if row and available else None,
            "bindingVersion": await binding_version(session, user.ontology_id, code), "supplementaryAvailable": available}


@router.get("/governed-bindings/{code}/supplementary-columns")
async def supplementary_columns(code: str, table: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    obj, _ = await object_for(session, user, code)
    source, _ = await inspect_table(session, table)
    return {"table": source.name, "columns": [{"name": c.name, "type": str(c.type)} for c in source.c],
            "properties": [{"api": p.api_name, "name": p.display_name, "column": p.backing_column or p.source_column}
                           for p in obj.properties if not p.is_sensitive and p.visibility != "hidden" and not p.transform
                           and (p.backing_column or p.source_column) in source.c]}


@router.put("/governed-bindings/{code}")
async def save(code: str, body: BindingIn, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    await get_adapter(session).xact_lock(session, f"governed.binding.{user.ontology_id}")
    obj, name = await object_for(session, user, code)
    main = await validate_binding(session, obj, name, body.read_columns, body.write_columns)
    available = await supplementary_available(session)
    if "supplementary" in body.model_fields_set and not available:
        raise invalid("补充表功能尚未完成数据库迁移")
    clash = await session.scalar(select(GovernedBinding).where(GovernedBinding.ontology_id == user.ontology_id, GovernedBinding.table_name == name, GovernedBinding.object_code != code))
    if clash or name in _GOVERNED or any(g.object_type == code for g in _GOVERNED.values()):
        raise invalid("此表或对象已由系统内置绑定，请先由管理员调整内置配置")
    if available:
        others = list(await session.scalars(select(GovernedBinding).where(GovernedBinding.ontology_id == user.ontology_id, GovernedBinding.object_code != code).options(undefer(GovernedBinding.supplementary))))
        if any((r.supplementary or {}).get("table") == name for r in others):
            raise invalid("主表已被其他对象用作补充表")
    else:
        others = []
    query = select(GovernedBinding).where(GovernedBinding.ontology_id == user.ontology_id, GovernedBinding.object_code == code).with_for_update()
    row = await session.scalar(query.options(undefer(GovernedBinding.supplementary)) if available else query)
    if ("supplementary" in body.model_fields_set or (row and available and row.supplementary)) and body.binding_version != await binding_version(session, user.ontology_id, code):
        raise invalid("绑定已变化，请刷新后重试")
    supplementary = body.supplementary if "supplementary" in body.model_fields_set else row.supplementary if row and available else None
    if supplementary:
        extra = await validate_supplementary(session, obj, main, body.read_columns, supplementary)
        if extra.name in _GOVERNED:
            raise invalid("补充表已由系统内置绑定")
        if any(r.table_name == extra.name or (r.supplementary or {}).get("table") in (name, extra.name) for r in others):
            raise invalid("主表或补充表已被其他对象绑定")
    if row is None:
        row = GovernedBinding(object_code=code, table_name=name, read_columns=[], write_columns=[])
        session.add(row)
    row.table_name, row.read_columns, row.write_columns = name, body.read_columns, body.write_columns
    if available and "supplementary" in body.model_fields_set:
        row.supplementary = supplementary
    await session.flush()
    _invalidate_bound(session, user.ontology_id)
    await load_governed_tables(session)
    return {"bound": True, "bindingVersion": await binding_version(session, user.ontology_id, code)}


@router.delete("/governed-bindings/{code}")
async def remove(code: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    await get_adapter(session).xact_lock(session, f"governed.binding.{user.ontology_id}")
    await session.execute(delete(GovernedBinding).where(GovernedBinding.ontology_id == user.ontology_id, GovernedBinding.object_code == code))
    await session.flush()
    _invalidate_bound(session, user.ontology_id)
    await load_governed_tables(session)
    return {"bound": False}
