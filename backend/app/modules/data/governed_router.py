"""Management API for explicit local table approvals."""
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Annotated
from sqlalchemy import delete, select, text
from sqlalchemy.orm import undefer
from app.common.auth import CurrentUserDep, check_func
from app.common.sql_guard import _GOVERNED, _invalidate_bound, load_governed_tables
from app.core.errors import AppError, Forbidden, NotFound
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.data.governed_binding import inspect_table, invalid, validate_binding, validate_supplementary
from app.modules.data.models import Datasource, DsType, ExternalReadBinding, GovernedBinding
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


class ExternalBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    version: int = Field(ge=0)
    read_columns: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(alias="readColumns", min_length=1, max_length=100)
    declared_unique_key: str | None = Field(default=None, alias="declaredUniqueKey", max_length=128)


async def external_binding_context(session, user, code):
    from app.modules.static.models import ObjectStatus
    from app.modules.qa.object_source import physical_identifier, primary_property
    authorize(user)
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == code))
    if not obj or obj.status != ObjectStatus.published or not obj.source_table or "." not in obj.source_table:
        raise invalid("对象须已发布并配置明确的外部 schema 和表")
    schema, table = obj.source_table.split(".", 1)
    if not physical_identifier(schema) or not physical_identifier(table):
        raise invalid("外部 schema 或表名无效")
    sources = list(await session.scalars(select(Datasource).where((Datasource.code == obj.datasource_name) | (Datasource.name == obj.datasource_name))))
    if len(sources) != 1 or sources[0].type not in (DsType.dm8, DsType.mysql):
        raise invalid("对象必须绑定唯一的达梦或 MySQL 数据源")
    source = sources[0]
    if source.type == DsType.dm8 and source.ssl:
        raise invalid("达梦 TLS 身份校验尚不可用，无法启用此只读绑定")
    from app.modules.data.ontology_import import inspect_source
    from app.modules.data.ontology_schemas import ImportPreviewIn
    _, tables, _ = await inspect_source(session, source.code, ImportPreviewIn(schema=schema, tables=[table]))
    physical = {column.name for column in tables[0].columns}
    allowed = {p.source_column for p in obj.properties if physical_identifier(p.source_column) and p.source_column in physical and not p.is_sensitive and p.visibility != "hidden" and not p.transform}
    primary = primary_property(obj)
    if primary is None or primary.source_column not in allowed:
        raise invalid("主键须映射到可公开读取的源字段")
    return obj, source, schema, table, allowed, primary.source_column


@router.get("/external-read-bindings/{code}")
async def external_detail(code: str, session: SessionDep, user: CurrentUserDep):
    obj, source, schema, table, allowed, primary = await external_binding_context(session, user, code)
    row = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code))
    from app.modules.data import connector
    from app.modules.data.external_cdc import cfg_for
    reflected = await connector.inspect_tables(source.type.value, cfg_for(source), schema, [table])
    return {"version": row.lock_version if row else 0, "datasourceId": source.code, "schema": schema, "table": table, "availableColumns": sorted(allowed), "requiredColumn": primary, "readColumns": row.read_columns if row else [],
            "sourceKind": reflected[0].kind, "declaredUniqueKey": row.declared_unique_key if row else None}


@router.put("/external-read-bindings/{code}")
async def external_save(code: str, body: ExternalBindingIn, session: SessionDep, user: CurrentUserDep, request: Request):
    from app.core.errors import VersionConflict
    from app.modules.admin.repo import insert_audit
    authorize(user)
    await get_adapter(session).xact_lock(session, f'external.mirror.{user.ontology_id}.{code}')
    from app.modules.data.external_cdc import require_paused, publish_metadata
    await get_adapter(session).xact_lock(session, f"external.read.binding.{user.ontology_id}.{code}")
    obj, source, schema, table, allowed, primary = await external_binding_context(session, user, code)
    row = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code).with_for_update())
    if (row.lock_version if row else 0) != body.version:
        raise VersionConflict()
    if len(set(body.read_columns)) != len(body.read_columns) or not set(body.read_columns) <= allowed or primary not in body.read_columns:
        raise invalid("读取字段须来自公开对象映射且包含主键")
    from app.modules.data import connector
    from app.modules.data.external_cdc import cfg_for
    cfg = cfg_for(source)
    reflected = await connector.inspect_tables(source.type.value, cfg, schema, [table])
    if reflected[0].kind == "view":
        if body.declared_unique_key != primary:
            raise AppError("视图须显式声明当前对象唯一键；同步时仍会验证空值和重复", code="VIEW_IDENTIFIER_UNAVAILABLE", status_code=422)
    elif body.declared_unique_key is not None:
        raise invalid("只有视图需要声明唯一键；表必须使用真实数据库主键")
    # Re-saving the same grant must not invalidate a healthy CDC baseline.
    if (row and (row.datasource_code, row.schema_name, row.table_name) == (source.code, schema, table)
            and set(row.read_columns) == set(body.read_columns)
            and row.declared_unique_key == body.declared_unique_key):
        return {"version": row.lock_version, "datasourceId": source.code, "schema": schema, "table": table,
                "readColumns": row.read_columns, "changed": False}
    cdc_sub = await require_paused(session, code)
    if reflected[0].kind == "view":
        await connector.validate_view_key(source.type.value, cfg, schema, table, primary)
    if row is None:
        row = ExternalReadBinding(object_code=code, datasource_code=source.code, schema_name=schema, table_name=table, read_columns=[], created_by=user.id)
        session.add(row)
    row.datasource_code, row.schema_name, row.table_name, row.read_columns = source.code, schema, table, body.read_columns
    row.declared_unique_key = body.declared_unique_key
    if cdc_sub:
        cdc_sub.restart_required, cdc_sub.phase = True, 'failed'
        cdc_sub.error_code, cdc_sub.error = 'CDC_BINDING_CHANGED', '读取绑定版本已变化，请重新计划并建立CDC基线'
        await publish_metadata(session, cdc_sub)
    await insert_audit(session, actor=f"{user.name}（{user.id}）", action="修改外部对象只读绑定", target=code, ip=request.client.host if request.client else "", created_by=user.id)
    await session.flush()
    return {"version": row.lock_version, "datasourceId": source.code, "schema": schema, "table": table,
            "readColumns": row.read_columns, "changed": True}


@router.delete("/external-read-bindings/{code}")
async def external_remove(code: str, version: int, session: SessionDep, user: CurrentUserDep, request: Request):
    from app.core.errors import VersionConflict
    from app.modules.admin.repo import insert_audit
    authorize(user)
    await get_adapter(session).xact_lock(session, f'external.mirror.{user.ontology_id}.{code}')
    from app.modules.data.external_cdc import require_paused, publish_metadata
    cdc_sub = await require_paused(session, code)
    await get_adapter(session).xact_lock(session, f"external.read.binding.{user.ontology_id}.{code}")
    row = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == code).with_for_update())
    if row is None or row.lock_version != version:
        raise VersionConflict()
    await session.delete(row)
    if cdc_sub:
        cdc_sub.restart_required, cdc_sub.phase = True, 'failed'
        cdc_sub.error_code, cdc_sub.error = 'CDC_BINDING_CHANGED', '读取绑定已撤销，CDC必须重新授权并建立基线'
        await publish_metadata(session, cdc_sub)
    await insert_audit(session, actor=f"{user.name}（{user.id}）", action="撤销外部对象只读绑定", target=code, ip=request.client.host if request.client else "", created_by=user.id)
    await session.flush()
    return {"bound": False}


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
    table, update_columns = await inspect_table(session, name)
    available = await supplementary_available(session)
    query = select(GovernedBinding).where(GovernedBinding.object_code == code, GovernedBinding.ontology_id == user.ontology_id)
    row = await session.scalar(query.options(undefer(GovernedBinding.supplementary)) if available else query)
    columns = []
    for p in obj.properties:
        column = p.backing_column or p.source_column
        if column in table.c and not p.is_sensitive and p.visibility != 'hidden' and not p.transform:
            columns.append({"column": column, "name": p.display_name, "api": p.api_name,
                            "writable": bool(column in update_columns and {'lock_version', 'updated_at'} <= update_columns and not p.is_pk and p.api_name != obj.pk_property and column not in {'id', 'code', 'ontology_id', 'lock_version', 'updated_at', 'created_by'})})
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
