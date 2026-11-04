"""Explicit source-database write grants and atomic remote receipts; never reuse read credentials."""

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr
from sqlalchemy import Integer, MetaData, Table, select
from sqlalchemy.exc import IntegrityError

from app.common.auth import CurrentUserDep, check_func
from app.common.ontology import require_ontology_id
from app.contracts.actions import ExtStatus, KeyQueryResult, SubmitResult, SubmitStatus
from app.core.errors import AppError, VersionConflict
from app.core.schema import ApiModel
from app.db.session import SessionDep
from app.modules.data import connector
from app.modules.data.models import Datasource, DsType
from app.modules.data.secrets import decrypt_password, encrypt_password
from app.modules.integration.models import (
    CallKind,
    ExtSystem,
    ExtSystemStatus,
    SourceWriteBinding,
    WritebackCall,
)
from app.modules.static.models import ObjectStatus, ObjectType

router = APIRouter(tags=["source-database"])
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,127}$")


def unavailable(message, code="SOURCE_WRITE_UNAVAILABLE"):
    return AppError(message, code=code, status_code=409)


def source_signature(source):
    return hashlib.sha256(
        json.dumps(
            [source.code, source.type.value, source.host, source.port, source.database, source.ssl]
        ).encode()
    ).hexdigest()


def writable_columns(obj):
    from app.modules.qa.object_source import primary_property

    primary = primary_property(obj)
    protected = {
        p.source_column
        for p in obj.properties
        if p.is_pk or p.is_sensitive or p.visibility == "hidden" or p.transform
    }
    if primary:
        protected.add(primary.source_column)
    return {
        p.source_column
        for p in obj.properties
        if p.source_column and IDENTIFIER.fullmatch(p.source_column) and p.source_column not in protected
    }


def authorize(user):
    check_func(user, "external-systems", "manage")
    check_func(user, "connections", "manage")
    check_func(user, "object-types", "view")
    check_func(user, "action-types", "view")
    if user.ontology_role != "owner":
        raise AppError("只有本体所有者可以显式授权源数据库写回", code="NO_PERMISSION", status_code=403)


class FieldMapping(ApiModel):
    column: str = Field(pattern=IDENTIFIER.pattern)
    parameter: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class BindingIn(ApiModel):
    version: int = Field(ge=0)
    datasource_id: str = Field(max_length=64)
    object_type_code: str = Field(max_length=64)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr | None = None
    receipt_table: str = Field(default="rengu_action_receipt", pattern=IDENTIFIER.pattern)
    enabled: bool = False
    action_id: str = Field(max_length=64)
    action_version: str = Field(max_length=16)
    mapping: list[FieldMapping] = Field(min_length=1, max_length=32)


async def binding(session, system_id):
    return await session.scalar(select(SourceWriteBinding).where(SourceWriteBinding.sys_id == system_id))


@router.get("/ext-systems/{code}/database-binding")
async def detail(code: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    from app.modules.action.models import ActionType

    oid = require_ontology_id(session)
    system = await session.scalar(
        select(ExtSystem).where(ExtSystem.code == code, ExtSystem.ontology_id == oid)
    )
    if system is None:
        raise unavailable("外部系统不存在")
    row = await binding(session, system.id)
    objects = []
    from app.modules.qa.object_source import primary_property

    for obj in await session.scalars(
        select(ObjectType).where(ObjectType.ontology_id == oid, ObjectType.status == ObjectStatus.published)
    ):
        primary = primary_property(obj)
        columns = sorted(writable_columns(obj))
        if primary and primary.source_column and obj.source_table:
            objects.append(
                dict(id=obj.code, name=obj.display_name, source=obj.datasource_name, columns=columns)
            )
    actions = [
        dict(
            id=a.code,
            name=a.name,
            version=a.version,
            object=a.target_kind,
            parameters=[dict(api=p.api, name=p.name) for p in a.params],
        )
        for a in await session.scalars(
            select(ActionType).where(
                ActionType.ontology_id == oid,
                ActionType.version.is_not(None),
                ActionType.retired_release_id.is_(None),
            )
        )
    ]
    value = (
        None
        if row is None
        else dict(
            version=row.lock_version,
            datasourceId=row.datasource_code,
            objectTypeCode=row.object_code,
            username=row.username,
            receiptTable=row.receipt_table,
            enabled=row.enabled,
            hasPassword=bool(row.password_enc),
            actions=row.actions,
        )
    )
    sources = [
        dict(id=s.code, name=s.name)
        for s in await session.scalars(
            select(Datasource).where(
                Datasource.ontology_id == oid, Datasource.type.in_((DsType.mysql, DsType.dm8))
            )
        )
    ]
    return dict(binding=value, datasources=sources, objects=objects, actions=actions)


@router.put("/ext-systems/{code}/database-binding")
async def save(code: str, body: BindingIn, session: SessionDep, user: CurrentUserDep, request: Request):
    authorize(user)
    from app.modules.action.models import ActionType
    from app.modules.admin.repo import insert_audit
    from app.modules.qa.object_source import primary_property

    oid = require_ontology_id(session)
    system = await session.scalar(
        select(ExtSystem).where(ExtSystem.code == code, ExtSystem.ontology_id == oid).with_for_update()
    )
    if system is None or system.endpoint != f"database://{body.datasource_id}":
        raise unavailable("数据库系统地址须为database://数据连接编号，并与选择的数据连接一致")
    row = await binding(session, system.id)
    if (row.lock_version if row else 0) != body.version:
        raise VersionConflict()
    source = await session.scalar(
        select(Datasource).where(Datasource.ontology_id == oid, Datasource.code == body.datasource_id)
    )
    obj = await session.scalar(
        select(ObjectType).where(ObjectType.ontology_id == oid, ObjectType.code == body.object_type_code)
    )
    if (
        source is None
        or source.type not in (DsType.mysql, DsType.dm8)
        or obj is None
        or obj.status != ObjectStatus.published
        or obj.datasource_name not in (source.code, source.name)
    ):
        raise unavailable("对象必须映射到当前本体已发布的唯一MySQL/DM数据源")
    parts = (obj.source_table or "").split(".")
    if len(parts) != 2 or not all(IDENTIFIER.fullmatch(part) for part in parts):
        raise unavailable("源对象必须声明明确schema与真实表")
    primary = primary_property(obj)
    allowed = writable_columns(obj)
    if (
        primary is None
        or not primary.source_column
        or primary.is_sensitive
        or primary.visibility == "hidden"
        or primary.transform
    ):
        raise unavailable("源对象主键不可用")
    from app.modules.data.external_cdc import cfg_for

    reflected = await connector.inspect_tables(source.type.value, cfg_for(source), parts[0], [parts[1]])
    if len(reflected) != 1 or reflected[0].kind != "table" or reflected[0].pk != [primary.source_column]:
        raise unavailable("数据库写回仅允许真实单列主键表，不能写视图或声明键")
    action = await session.scalar(
        select(ActionType).where(ActionType.ontology_id == oid, ActionType.code == body.action_id)
    )
    if (
        action is None
        or action.retired_release_id is not None
        or action.version != body.action_version
        or action.target_kind != obj.code
        or action.sys_id != system.id
    ):
        raise unavailable("请先把当前已发布动作绑定到该系统，再授权相同版本和目标对象")
    mappings = {item.column: item.parameter for item in body.mapping}
    if (
        len(mappings) != len(body.mapping)
        or not mappings.keys() <= allowed
        or not set(mappings.values()) <= {p.api for p in action.params}
    ):
        raise unavailable("仅允许映射当前动作参数到对象公开非主键的授权写列")
    if body.username == source.username:
        raise unavailable("必须使用独立写回账号，不能复用数据连接的只读/CDC账号")
    password = body.password.get_secret_value() if body.password else ""
    if not password and (row is None or row.datasource_code != source.code or row.username != body.username):
        raise unavailable("新写账号或数据源必须提供独立写凭据")
    if row is None:
        row = SourceWriteBinding(sys_id=system.id, created_by=user.id)
        session.add(row)
    row.datasource_code, row.object_code = source.code, obj.code
    row.source_signature = source_signature(source)
    row.schema_name, row.table_name, row.key_column = *parts, primary.source_column
    if row.table_name == body.receipt_table:
        raise unavailable("业务表与回执表必须不同")
    row.receipt_table, row.username = body.receipt_table, body.username
    if password:
        row.password_enc = encrypt_password(password)
    row.write_columns, row.enabled = list(mappings), body.enabled
    row.actions = {action.code: {"version": action.version, "fields": mappings}}
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="配置独立源数据库写回授权",
        target=code,
        ip=request.client.host if request.client else "",
        created_by=user.id,
    )
    await session.flush()
    return {"version": row.lock_version, "enabled": row.enabled, "hasPassword": bool(row.password_enc)}


@dataclass
class Plan:
    config: dict
    kind: str
    schema: str
    table: str
    key_column: str
    receipt_table: str
    receipt_id: str
    request_hash: str
    run_id: str
    object_key: str
    values: dict
    path: str
    idem_key: str | None = None
    progress: dict = field(
        default_factory=lambda: {"phase": "prepare", "commitStarted": False, "committed": False}, repr=False
    )


def safe_error(plan, exc):
    current, seen, number = exc, set(), None
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        number = next(
            (arg for arg in getattr(current, "args", ()) if type(arg) is int and -999999 <= arg <= 4999),
            number,
        )
        current = getattr(current, "orig", None) or current.__cause__ or current.__context__
    name = re.sub(r"[^A-Za-z0-9_]", "", type(exc).__name__)[:64]
    return f"phase={plan.progress['phase']}; class={name}" + (
        f"; errno={number}" if number is not None else ""
    )


def target_metadata(conn, plan):
    table = Table(plan.table, MetaData(), schema=plan.schema, autoload_with=conn, resolve_fks=False)
    if [c.name for c in table.primary_key] != [plan.key_column] or not plan.values.keys() <= set(
        table.c.keys()
    ):
        raise unavailable("源表主键或授权列结构已变化")
    return table


async def execution_available(session, user, system, definition):
    """Metadata/configuration only, with the same source grant and governance as execution."""
    from types import SimpleNamespace

    from app.modules.governance.source_write import preflight as source_policy

    if (
        definition.get("sysId") != system.code
        or definition.get("httpMapping")
        or definition.get("httpEndpointId")
    ):
        raise unavailable("数据库动作不能混用HTTP接口映射")
    await source_policy(
        session,
        user,
        SimpleNamespace(obj_kind=definition.get("targetKind"), type_version=definition.get("version")),
        definition,
    )
    grant = await binding(session, system.id)
    if grant is None:
        raise unavailable("独立数据库授权不存在")
    source = await session.scalar(select(Datasource).where(Datasource.code == grant.datasource_code))
    if source is None:
        raise unavailable("源数据连接已不可用")
    if not decrypt_password(grant.password_enc) or not grant.username or grant.username == source.username:
        raise unavailable("独立写凭据不可用")
    cfg = dict(host=source.host, port=source.port, database=source.database, ssl=source.ssl)
    if not connector.supports(source.type.value) or not connector.permitted(source.type.value, cfg):
        raise unavailable("源数据库驱动或部署网络授权不可用")
    if (
        connector.real_connect_enabled()
        and source.ssl
        and str(source.host) != str(connector._approved_ip(cfg))
    ):
        raise unavailable("源数据库TLS主机身份尚未获授权")


async def prepare(session, system, run, *, query_only=False):
    row = await binding(session, system.id)
    if row is None or (not query_only and (not row.enabled or system.status == ExtSystemStatus.disabled)):
        raise unavailable("此系统尚未显式启用源数据库写回")
    if system.endpoint != f"database://{row.datasource_code}" or not system.idempotent:
        raise unavailable("数据库系统地址或幂等声明已变化")
    source = await session.scalar(select(Datasource).where(Datasource.code == row.datasource_code))
    if (
        source is None
        or source.type not in (DsType.mysql, DsType.dm8)
        or source_signature(source) != row.source_signature
    ):
        raise unavailable("源数据库身份已变化，请重新核对授权")
    entry = row.actions.get(run.action_type_code)
    if (
        run.obj_kind != row.object_code
        or not isinstance(entry, dict)
        or entry.get("version") != run.type_version
    ):
        raise unavailable("动作/对象/固定版本不在源数据库授权中", "SOURCE_WRITE_NOT_AUTHORIZED")
    fields = entry.get("fields", {})
    if not fields or not fields.keys() <= set(row.write_columns):
        raise unavailable("数据库写列授权已变化")
    if not query_only:
        from app.modules.qa.object_source import primary_property

        obj = await session.scalar(select(ObjectType).where(ObjectType.code == row.object_code))
        primary = primary_property(obj) if obj else None
        public = writable_columns(obj) if obj else set()
        if (
            obj is None
            or obj.status != ObjectStatus.published
            or obj.datasource_name not in (source.code, source.name)
            or obj.source_table != f"{row.schema_name}.{row.table_name}"
            or primary is None
            or primary.source_column != row.key_column
            or not fields.keys() <= public
        ):
            raise unavailable("当前对象/源映射或字段可写授权已变化，未执行写入")
    params = run.params or {}
    if any(name not in params for name in fields.values()):
        raise unavailable("数据库写回缺少映射参数")
    values = {column: params[parameter] for column, parameter in fields.items()}
    if any(value is not None and type(value) not in (str, int, bool, float) for value in values.values()):
        raise unavailable("数据库写回字段只接受明确标量参数")
    cfg = dict(
        host=source.host,
        port=source.port,
        database=source.database,
        ssl=source.ssl,
        user=row.username,
        password=decrypt_password(row.password_enc),
    )
    if not cfg["password"] or row.username == source.username:
        raise unavailable("独立写凭据不可用")
    key = hashlib.sha256(f"{require_ontology_id(session)}:{system.id}:{run.idem_key}".encode()).hexdigest()
    digest = hashlib.sha256(
        json.dumps(
            [run.code, run.action_type_code, run.type_version, run.obj_id, values],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return Plan(
        cfg,
        source.type.value,
        row.schema_name,
        row.table_name,
        row.key_column,
        row.receipt_table,
        key,
        digest,
        run.code,
        run.obj_id,
        values,
        f"database://{system.code}/{row.id}/{row.lock_version}",
        idem_key=run.idem_key,
    )


def operation(conn, plan, *, read_only=False):
    """Only declared identifiers and bound scalar values; business update and receipt share one commit."""
    plan.progress["phase"] = "receipt_metadata"
    receipt = Table(plan.receipt_table, MetaData(), schema=plan.schema, autoload_with=conn, resolve_fks=False)
    required = {"receipt_id", "run_id", "request_hash", "outcome", "result_code", "reason"}
    if not required <= set(receipt.c.keys()) or [c.name for c in receipt.primary_key] != ["receipt_id"]:
        raise unavailable("源回执表不符合幂等合同；必须由DBA预先创建")
    query = select(receipt).where(receipt.c.receipt_id == plan.receipt_id)
    plan.progress["phase"] = "receipt_lookup"
    # Receipts are immutable and INSERT-only. Their unique PK arbitrates concurrent writers;
    # locking this read would unnecessarily require UPDATE privileges on older MySQL.
    existing = conn.execute(query).mappings().one_or_none()
    if existing:
        if existing["request_hash"] != plan.request_hash or existing["run_id"] != plan.run_id:
            raise unavailable("源回执幂等键对应的请求不一致", "IDEMPOTENCY_CONFLICT")
        if existing["outcome"] not in ("SUCCESS", "FAILED"):
            raise unavailable("源回执不是可确认终态，须继续对账")
        return dict(existing)
    if read_only:
        return None
    plan.progress["phase"] = "target_metadata"
    table = target_metadata(conn, plan)
    if conn.dialect.name == "mysql":
        from sqlalchemy import text

        plan.progress["phase"] = "transactional_storage"
        engines = list(
            conn.execute(
                text(
                    "SELECT TABLE_NAME, ENGINE FROM information_schema.tables "
                    "WHERE TABLE_SCHEMA=:schema AND TABLE_NAME IN (:target,:receipt)"
                ),
                dict(schema=plan.schema, target=plan.table, receipt=plan.receipt_table),
            )
        )
        if len(engines) != 2 or any(row[1] != "InnoDB" for row in engines):
            raise unavailable("业务表和回执表必须都是InnoDB事务表")
    key = int(plan.object_key) if isinstance(table.c[plan.key_column].type, Integer) else plan.object_key
    plan.progress["phase"] = "target_lookup"
    targets = list(
        conn.execute(
            select(table.c[plan.key_column]).where(table.c[plan.key_column] == key).with_for_update()
        )
    )
    if len(targets) > 1:
        raise unavailable("源目标身份不唯一")
    result = dict(
        receipt_id=plan.receipt_id,
        run_id=plan.run_id,
        request_hash=plan.request_hash,
        outcome="SUCCESS" if targets else "FAILED",
        result_code="SOURCE_UPDATED" if targets else "TARGET_NOT_FOUND",
        reason="源数据库事务已更新" if targets else "源目标不存在，未执行更新",
    )
    plan.progress["phase"] = "receipt_insert"
    conn.execute(receipt.insert().values(**result))
    if targets:
        plan.progress["phase"] = "target_update"
        conn.execute(table.update().where(table.c[plan.key_column] == key).values(**plan.values))
    return result


async def perform(plan, *, read_only=False, probe=False):
    plan.progress = {"phase": "network_validation", "commitStarted": False, "committed": False}
    cfg = dict(plan.config)
    if not connector.permitted(plan.kind, cfg):
        raise unavailable("源数据库地址未获部署授权")
    if connector.real_connect_enabled():
        pinned = connector._approved_ip(cfg)
        if not pinned or (cfg.get("ssl") and str(cfg["host"]) != str(pinned)):
            raise unavailable("源数据库地址/TLS身份尚未获授权")
        cfg["host"] = pinned

    def sync():
        plan.progress["phase"] = "engine"
        engine = connector._engine(plan.kind, cfg)
        plan.progress["threadInFlight"] = True
        try:
            plan.progress["phase"] = "connect"
            with engine.begin() as connection:
                if read_only:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                if probe:
                    plan.progress["phase"] = "target_metadata"
                    target_metadata(connection, plan)
                result = operation(connection, plan, read_only=read_only)
                plan.progress.update(phase="commit", commitStarted=not read_only)
            plan.progress.update(phase="committed", committed=not read_only)
            return result
        finally:
            try:
                engine.dispose()
            finally:
                plan.progress["threadInFlight"] = False

    if plan.kind == "达梦 DM8":
        return await asyncio.to_thread(sync)
    plan.progress["phase"] = "engine"
    engine = connector._engine(plan.kind, cfg)
    try:
        plan.progress["phase"] = "connect"
        async with engine.connect() as connection:
            if read_only:
                await connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")
                await connection.rollback()
            async with connection.begin():
                if probe:
                    plan.progress["phase"] = "target_metadata"
                    await connection.run_sync(lambda conn: target_metadata(conn, plan))
                result = await connection.run_sync(lambda conn: operation(conn, plan, read_only=read_only))
                plan.progress.update(phase="commit", commitStarted=not read_only)
            plan.progress.update(phase="committed", committed=not read_only)
            return result
    finally:
        await engine.dispose()


async def submit(plan):
    try:
        async with asyncio.timeout(60):
            result = await perform(plan)
    except Exception as exc:
        if isinstance(exc, IntegrityError) and plan.progress.get("phase") == "receipt_insert":
            original = safe_error(plan, exc)
            # perform has exited/rolled back the failed transaction before this read.
            # A concurrent canonical writer may have committed; never issue a second UPDATE.
            try:
                async with asyncio.timeout(60):
                    receipt = await perform(plan, read_only=True)
                if receipt is not None:
                    return SubmitResult(
                        SubmitStatus.SUCCESS if receipt["outcome"] == "SUCCESS" else SubmitStatus.FAILED,
                        receipt_id=receipt["receipt_id"],
                        reason=receipt["reason"],
                        code=receipt["result_code"],
                    )
            except AppError as query_error:
                if query_error.code == "IDEMPOTENCY_CONFLICT":
                    return SubmitResult(
                        SubmitStatus.FAILED,
                        reason="源回执对应不同请求，未执行本次写入",
                        code="IDEMPOTENCY_CONFLICT",
                    )
            except Exception:
                pass
            return SubmitResult(
                SubmitStatus.UNKNOWN,
                reason="源回执写入冲突，尚未核实原请求终态；只查询回执，不重发（" + original + "）",
                code="SOURCE_DB_RECEIPT_CONFLICT_UNKNOWN",
            )
        unknown = plan.progress.get("commitStarted", False) or plan.progress.get("threadInFlight", False)
        return SubmitResult(
            SubmitStatus.UNKNOWN if unknown else SubmitStatus.FAILED,
            reason=(
                "源数据库提交结果无法确认；只查询幂等回执，不重发业务写入"
                if unknown
                else "源事务未提交，未完成业务写入"
            )
            + "（"
            + safe_error(plan, exc)
            + "）",
            code="SOURCE_DB_UNKNOWN" if unknown else "SOURCE_DB_PRECOMMIT_FAILED",
        )
    status = SubmitStatus.SUCCESS if result["outcome"] == "SUCCESS" else SubmitStatus.FAILED
    return SubmitResult(
        status, receipt_id=result["receipt_id"], reason=result["reason"], code=result["result_code"]
    )


class ReceiptAdapter:
    last_http = None

    def __init__(self, plan):
        self.plan = plan

    def path(self, operation):
        return self.plan.path

    async def query_by_key(self, key):
        if key != self.plan.idem_key:
            raise unavailable("对账幂等键与原执行不一致")
        try:
            async with asyncio.timeout(60):
                row = await perform(self.plan, read_only=True)
        except Exception as exc:
            raise unavailable(
                "无法核实源数据库回执；未重发业务写入（" + safe_error(self.plan, exc) + "）",
                "SOURCE_DB_QUERY_FAILED",
            ) from None
        if row is None:
            return KeyQueryResult(ExtStatus.not_found)
        return KeyQueryResult(
            ExtStatus.found_succeeded if row["outcome"] == "SUCCESS" else ExtStatus.found_not_executed,
            row["receipt_id"],
            {"reason": row["reason"], "code": row["result_code"]},
        )


async def query_adapter(session, system, run):
    plan = await prepare(session, system, run, query_only=True)
    call = await session.scalar(
        select(WritebackCall).where(
            WritebackCall.action_run_id == run.id, WritebackCall.kind == CallKind.write
        )
    )
    if call is None or call.path != plan.path:
        raise unavailable("数据库授权版本已变化，原回执须人工核实；不会查询新目标")
    return ReceiptAdapter(plan)


@router.post("/ext-systems/{code}/database-binding:probe")
async def probe_binding(code: str, session: SessionDep, user: CurrentUserDep):
    authorize(user)
    system = await session.scalar(select(ExtSystem).where(ExtSystem.code == code))
    if system is None:
        raise unavailable("外部系统不存在")
    row = await binding(session, system.id)
    if row is None:
        raise unavailable("请先保存独立写回授权")
    source = await session.scalar(select(Datasource).where(Datasource.code == row.datasource_code))
    if source is None or source_signature(source) != row.source_signature:
        raise unavailable("源身份已变化")
    cfg = dict(
        host=source.host,
        port=source.port,
        database=source.database,
        ssl=source.ssl,
        user=row.username,
        password=decrypt_password(row.password_enc),
    )
    plan = Plan(
        cfg,
        source.type.value,
        row.schema_name,
        row.table_name,
        row.key_column,
        row.receipt_table,
        "0" * 64,
        "0" * 64,
        "PROBE_ONLY",
        "",
        {column: None for column in row.write_columns},
        "",
    )
    try:
        await perform(plan, read_only=True, probe=True)
    except Exception as exc:
        raise unavailable("无法读取授权源表/回执结构；未执行写入（" + safe_error(plan, exc) + "）") from None
    return {"ok": True, "message": "源表与幂等回执结构可读取；未执行写入，不代表UPDATE权限已实测"}
