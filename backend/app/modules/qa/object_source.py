"""Opt-in, workspace-specific SELECT adapters. Never infer a physical binding from an object name."""

import json
import os
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    MetaData,
    Numeric,
    String,
    Table,
    select,
    text,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import Select

from app.common.auth import CurrentUserDep, check_func, row_filter
from app.common.ontology import require_ontology_id
from app.common.row_filter import is_deny_all
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.db.session import qa_readonly_engine_kwargs
from app.modules.data.models import Datasource, DsType
from app.modules.qa.readonly import scope_readonly_conn
from app.modules.static.explorer import snapshot_flags
from app.modules.static.models import ObjectStatus, ObjectType, Property


def check_object_read(user):
    if not any(user.func.get(page) in ("view", "manage") for page in ("object-search", "object-detail")):
        check_func(user, "object-detail", "view")


async def require_object_read(user: CurrentUserDep):
    check_object_read(user)
    return user


async def can_export_object(session, user, code):
    """Current workspace grants, keyed by stable API identity, never a display label."""
    from app.modules.admin.models import SysRole, SysRoleMember, SysUser, UserStatus
    from app.modules.ontology.models import OntologyRoleBinding

    if getattr(user, "ontology_ids", None) is not None or is_deny_all(await row_filter(user, code)):
        return False
    api = await session.scalar(
        select(ObjectType.api_name).where(
            ObjectType.code == code, ObjectType.status == ObjectStatus.published
        )
    )
    if api is None:
        return False
    permissions = await session.scalars(
        select(SysRole.objs)
        .join(SysRoleMember, SysRoleMember.role_id == SysRole.id)
        .join(SysUser, SysUser.id == SysRoleMember.user_id)
        .join(OntologyRoleBinding, OntologyRoleBinding.role_id == SysRole.id)
        .where(
            SysUser.code == user.id,
            SysUser.status == UserStatus.active,
            OntologyRoleBinding.ontology_id == require_ontology_id(session),
        )
    )
    for rows in permissions:
        matches = (
            [entry for entry in rows if isinstance(entry, dict) and entry.get("api") == api]
            if isinstance(rows, list)
            else []
        )
        if len(matches) == 1 and matches[0].get("view") is True and matches[0].get("export") is True:
            return True
    return False


def unavailable(message="对象数据源读取失败，请核对只读绑定及源表映射"):
    return AppError(message, code="OBJECT_DATA_UNAVAILABLE", status_code=503)


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[^\W\d]\w{0,127}", value))


def physical_identifier(value):
    """Single metadata-derived SQL identifier; SQLAlchemy supplies dialect quoting."""
    return isinstance(value, str) and bool(re.fullmatch(r"\w{1,128}", value))


def primary_property(obj):
    explicit = next((p for p in obj.properties if p.api_name == obj.pk_property), None)
    if explicit is not None:
        return explicit
    flagged = [p for p in obj.properties if p.is_pk]
    # Legacy retail metadata stores a physical name (sku_code) alongside logical sku.
    if len(flagged) == 1 and obj.pk_property in (None, "", flagged[0].source_column):
        return flagged[0]
    return None


def configured_bindings(ontology_id=None):
    if os.environ.get("RENGU_OBJECT_READ_ENABLED") != "1":
        return []
    path = os.environ.get("RENGU_OBJECT_READ_BINDINGS")
    if not path:
        return []
    issue = "配置文件无法读取或不是有效 JSON"
    try:
        file = Path(path)
        if file.stat().st_size > 1024 * 1024:
            issue = "配置文件超过 1 MB"
            raise ValueError()
        rows = json.loads(file.read_text(encoding="utf-8-sig"))
        issue = "配置内容必须为绑定列表"
        if not isinstance(rows, list):
            raise ValueError()
        keys = set()
        selected = []
        for index, row in enumerate(rows, 1):
            if isinstance(row, dict) and type(row.get("ontologyId")) is int and ontology_id is not None and row["ontologyId"] != ontology_id:
                continue
            issue = f"第 {index} 条绑定的字段不完整或包含未知字段"
            if not isinstance(row, dict) or set(row) != {
                "ontologyId",
                "objectType",
                "datasourceId",
                "schema",
                "table",
                "columns",
                "urlEnv",
            }:
                raise ValueError()
            issue = f"第 {index} 条绑定的本体编号无效"
            if type(row["ontologyId"]) is not int or row["ontologyId"] <= 0:
                raise ValueError()
            issue = f"第 {index} 条绑定的对象、数据源或连接配置名称为空"
            if not all(isinstance(row[k], str) and row[k] for k in ("objectType", "datasourceId", "urlEnv")):
                raise ValueError()
            issue = f"第 {index} 条绑定的 schema 或表名无效"
            if (
                not identifier(row["schema"])
                or not identifier(row["table"])
                or row["schema"].startswith("pg_")
                or row["schema"] == "information_schema"
            ):
                raise ValueError()
            issue = f"第 {index} 条绑定的读取列无效或为空"
            if (
                not isinstance(row["columns"], list)
                or not row["columns"]
                or not all(identifier(c) for c in row["columns"])
            ):
                raise ValueError()
            issue = f"第 {index} 条绑定的读取列重复或连接配置名称无效"
            if len(set(row["columns"])) != len(row["columns"]) or not re.fullmatch(
                r"[A-Z][A-Z0-9_]{0,127}", row["urlEnv"]
            ):
                raise ValueError()
            key = (row["ontologyId"], row["objectType"])
            issue = f"第 {index} 条绑定与同本体的对象绑定重复"
            if key in keys:
                raise ValueError()
            keys.add(key)
            selected.append(row)
        return selected
    except (OSError, UnicodeError, ValueError, TypeError):
        raise unavailable(f"对象只读绑定配置不合法：{issue}；未执行数据读取") from None


async def object_definition(session, code, ver=None):
    if not ver:
        obj = await session.scalar(select(ObjectType).where(ObjectType.code == code))
        if obj is None or obj.status != ObjectStatus.published:
            raise NotFound("已发布对象类型不存在", details={"objectType": code})
        return obj
    from app.modules.versioning.repo import ReleaseRepo

    release = await ReleaseRepo(session).by_ver_or_404(ver)
    snap = release.snapshot or {}
    row = (snap.get("objectTypes") or {}).get(code)
    if not row or row.get("status") != "已发布":
        raise NotFound("该版本中已发布对象类型不存在", details={"objectType": code, "ver": ver})
    props = [
        Property(
            api_name=api,
            display_name=p["name"],
            data_type=p["type"],
            prop_group=p["group"],
            source_column=p.get("source") or "",
            description=p.get("desc") or "",
            is_pk=bool(p.get("pk")),
            is_title=bool(p.get("title")),
            is_dimension=bool(p.get("dim")),
            is_sensitive=bool(p.get("sensitive")),
            enum_name=p.get("enumName"),
            **snapshot_flags(code, p, n, require_ontology_id(session)),
        )
        for n, (api, p) in enumerate((snap.get("properties") or {}).get(code, {}).items())
    ]
    return ObjectType(
        code=code,
        api_name=row["api"],
        display_name=row["name"],
        status=ObjectStatus.published,
        datasource_name=row.get("source"),
        source_table=row.get("table"),
        pk_property=row.get("pk"),
        properties=props,
    )


TYPES = {
    "integer": BigInteger,
    "int": BigInteger,
    "decimal": Numeric,
    "boolean": Boolean,
    "date": Date,
    "timestamp": lambda: DateTime(timezone=True),
    "string": String,
    "enum": String,
}


@dataclass
class ObjectSource:
    table: Table
    columns: dict
    id_column: str
    url: object
    datasource: str
    ontology_id: int

    @property
    def is_dm(self):
        return getattr(self.url, "drivername", None) == "dm+dmPython"

    @asynccontextmanager
    async def read(self):
        if self.is_dm:
            from app.modules.data import connector
            if not connector.real_connect_enabled():
                raise unavailable("达梦只读连接未启用")
            cfg = {"host": self.url.host, "port": self.url.port or 5236, "database": self.url.database,
                   "user": self.url.username, "password": self.url.password, "ssl": False}
            pinned = await asyncio.to_thread(connector._approved_ip, cfg)
            if pinned is None:
                raise unavailable("达梦只读连接地址未获部署授权")
            cfg["host"] = pinned
            loop = asyncio.get_running_loop()
            executor = ThreadPoolExecutor(max_workers=1)
            state = {}

            def open_readonly():
                eng = connector._engine("达梦 DM8", cfg)
                try:
                    conn = eng.connect()
                    try:
                        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                    except BaseException:
                        conn.close()
                        raise
                    state.update(engine=eng, connection=conn)
                except BaseException:
                    eng.dispose()
                    raise

            class Reader:
                async def execute(self, stmt):
                    if not isinstance(stmt, Select):
                        raise unavailable("达梦对象读取仅支持受控 SELECT")
                    return await loop.run_in_executor(executor, lambda: state["connection"].execute(stmt).freeze()())

            def close_readonly():
                if "connection" in state:
                    state["connection"].close()
                if "engine" in state:
                    state["engine"].dispose()

            try:
                await loop.run_in_executor(executor, open_readonly)
                yield Reader()
            except AppError:
                raise
            except Exception:
                raise unavailable("达梦对象只读连接或查询失败") from None
            finally:
                try:
                    await asyncio.shield(loop.run_in_executor(executor, close_readonly))
                finally:
                    executor.shutdown(wait=True)
            return
        options = qa_readonly_engine_kwargs(get_settings().qa_statement_timeout_ms)
        options["connect_args"]["timeout"] = 10
        options["connect_args"]["server_settings"]["timezone"] = get_settings().display_tz
        engine = None
        try:
            engine = create_async_engine(
                self.url,
                poolclass=NullPool,
                isolation_level="REPEATABLE READ",
                execution_options={"postgresql_readonly": True},
                **options,
            )
            async with engine.connect() as conn, conn.begin():
                # 先收窄到本体角色（外部库没有该角色时不切），再查生效身份不是超级用户 / BYPASSRLS
                await scope_readonly_conn(
                    conn,
                    self.ontology_id,
                    error=lambda: unavailable("对象只读连接账号不能具有超级用户或绕过行权限能力"),
                )
                role = (
                    (
                        await conn.execute(
                            text(
                                "SELECT rolsuper, rolbypassrls, "
                                "current_setting('transaction_read_only') AS readonly "
                                "FROM pg_roles WHERE rolname = current_user"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                if role["rolsuper"] or role["rolbypassrls"] or role["readonly"] != "on":
                    raise unavailable("对象只读连接账号不能具有超级用户或绕过行权限能力")
                yield conn
        except AppError:
            raise
        except Exception as exc:
            # Driver errors may contain credentials, hosts or raw SQL. Never expose them.
            if getattr(getattr(exc, "orig", None), "sqlstate", None) == "42501":
                raise unavailable("对象只读账号无源表读取权限，请管理员为该表授予 SELECT 后重试") from None
            raise unavailable() from None
        finally:
            if engine is not None:
                await engine.dispose()


async def require_external_schema_scope(session, obj):
    from app.modules.data.connector import require_configured_schema
    from app.modules.data.models import ExternalReadBinding
    binding = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == obj.code))
    if binding is None:
        return
    ds = await session.scalar(select(Datasource).where(Datasource.code == binding.datasource_code))
    if ds is None:
        raise unavailable("外部对象的数据源不存在")
    try:
        require_configured_schema(ds.type.value, {"database": ds.database}, [binding.schema_name])
    except ValueError:
        raise unavailable("达梦对象绑定不在数据源限定的 schema 内") from None


async def resolve_source(session, obj, *, allow_dm=False):
    ontology_id = require_ontology_id(session)
    from app.modules.data.models import ExternalReadBinding
    persisted = await session.scalar(select(ExternalReadBinding).where(ExternalReadBinding.object_code == obj.code)) if allow_dm else None
    item = ({"ontologyId": ontology_id, "objectType": obj.code, "datasourceId": persisted.datasource_code,
             "schema": persisted.schema_name, "table": persisted.table_name, "columns": persisted.read_columns}
            if persisted else next(
        (
            row
            for row in configured_bindings(ontology_id)
            if row["ontologyId"] == ontology_id and row["objectType"] == obj.code
        ),
        None,
    ))
    if item is None:
        if allow_dm and obj.datasource_name:
            source_types = list(await session.scalars(select(Datasource.type).where(
                (Datasource.code == obj.datasource_name) | (Datasource.name == obj.datasource_name)
            )))
            if DsType.dm8 in source_types:
                raise unavailable("达梦对象尚未配置持久只读绑定")
        return None
    if persisted and (not physical_identifier(item["schema"]) or not physical_identifier(item["table"]) or
                      not isinstance(item["columns"], list) or not item["columns"] or
                      not all(physical_identifier(column) for column in item["columns"])):
        raise unavailable("外部只读绑定包含无效物理标识符")
    ds = await session.scalar(select(Datasource).where(Datasource.code == item["datasourceId"]))
    if ds is not None and ds.type == DsType.dm8 and not persisted:
        raise unavailable("达梦对象尚未配置持久只读绑定")
    if ds is not None and ds.type == DsType.dm8 and not allow_dm:
        raise unavailable("达梦对象读取尚未支持安全只读事务与行权限校验；结构导入不代表对象数据可读取")
    if (
        ds is None
        or obj.datasource_name not in (ds.code, ds.name)
        or ds.type not in (DsType.postgresql, DsType.kingbase, DsType.dm8)
    ):
        raise unavailable("对象数据源与获准只读绑定不一致")
    if obj.datasource_name != ds.code:
        names = list(
            await session.scalars(select(Datasource.code).where(Datasource.name == obj.datasource_name))
        )
        if names != [ds.code]:
            raise unavailable("对象数据源名称不唯一，请使用稳定数据源编号")
    source_table = f"{item['schema']}.{item['table']}"
    if obj.source_table not in (source_table, item["table"] if item["schema"] == "public" else source_table):
        raise unavailable("对象来源表已变化，请更新获准只读绑定")
    if persisted:
        from app.modules.data.secrets import decrypt_password
        from app.modules.data.connector import require_configured_schema
        try:
            require_configured_schema(ds.type.value, {"database": ds.database}, [item["schema"]])
        except ValueError:
            raise unavailable("达梦对象绑定不在数据源限定的 schema 内") from None
        password = decrypt_password(ds.password_enc)
        if ds.type != DsType.dm8 or not password or not ds.username or ds.ssl:
            raise unavailable("达梦数据源凭据缺失或 TLS 身份校验不可用")
        url = URL.create("dm+dmPython", username=ds.username, password=password, host=ds.host, port=ds.port, database=ds.database or None)
    else:
        raw_url = os.environ.get(item["urlEnv"])
        if not raw_url:
            return None
        try:
            url = make_url(raw_url)
        except (ArgumentError, ValueError, TypeError):
            raise unavailable("专用只读连接配置无效") from None
    try:
        driver = "dm+dmPython" if ds.type == DsType.dm8 else "postgresql+asyncpg"
        if ds.type == DsType.dm8 and (not allow_dm or ds.ssl):
            raise ValueError()
        if url.drivername != driver or (url.host, url.port or (5236 if ds.type == DsType.dm8 else 5432), url.database) != (
            ds.host,
            ds.port,
            ds.database or None,
        ):
            raise ValueError()
        if not url.username or not url.password or url.query:
            raise ValueError()
    except (ArgumentError, ValueError, TypeError):
        raise unavailable("专用只读连接与登记数据源不一致，未执行读取") from None
    columns = {}
    types = {}
    for prop in obj.properties:
        source = prop.source_column
        if (
            source in item["columns"]
            and (physical_identifier(source) if persisted else identifier(source))
            and prop.data_type in TYPES
            and not prop.transform
        ):
            columns[prop.api_name] = source
            types[source] = TYPES[prop.data_type]()
    primary = primary_property(obj)
    pk = primary.api_name if primary is not None else None
    if not pk or pk not in columns:
        return None
    table = Table(
        item["table"], MetaData(), *(Column(name, typ) for name, typ in types.items()), schema=item["schema"]
    )
    return ObjectSource(table, columns, columns[pk], url, ds.code, ontology_id)
