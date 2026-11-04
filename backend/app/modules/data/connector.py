"""真实连接数据源：测试连接 + 库表内省（数据连接页「测试连接」和「库表」页签）。

什么时候真连：`RENGU_DS_REAL_CONNECT=1`，或 env = prod。否则服务明确返回未开启，不模拟成功。

配置 PostgreSQL/金仓 PG 协议、MySQL 与达梦驱动；可选驱动未安装时明确失败，不模拟成功。

内省复用 auto_ontology 的思路（SQLAlchemy inspect，方言无关），但只取清单要的东西：
schema、表名、表注释、字段数、估算行数（PG 用 pg_class.reltuples，不 count(*)）。
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError, NoSuchModuleError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.modules.auto_ontology.introspect import SYSTEM_TABLES, Table, mysql_catalog_columns, readable_table_stats
from app.modules.auto_ontology.introspect import inspect_tables as inspect_connection_tables
from app.modules.data.adapters import ConnectionTestResult

CONNECT_TIMEOUT_S = 10
MAX_TABLES = 2000
SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast", "sys", "sys_catalog"})


class _PinnedHostnameContext(ssl.SSLContext):
    """Verify the approved original hostname while asyncpg connects to a pinned IP."""

    def __new__(cls, hostname: str):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self, hostname: str):
        self._expected_hostname = hostname
        self.load_default_certs()

    def wrap_bio(self, incoming, outgoing, server_side=False, server_hostname=None, session=None):
        if server_side:
            raise ValueError("client TLS context cannot be used as a server")
        return super().wrap_bio(incoming, outgoing, server_side=False, server_hostname=self._expected_hostname, session=session)

# 类型（中文名，DsType.value）→ SQLAlchemy 异步驱动
DRIVERS: dict[str, str] = {
    "PostgreSQL": "postgresql+asyncpg",
    "人大金仓 KingbaseES": "postgresql+asyncpg",
    "MySQL": "mysql+asyncmy",
    "达梦 DM8": "dm+dmPython",
}


def real_connect_enabled() -> bool:
    return os.environ.get("RENGU_DS_REAL_CONNECT") == "1" or get_settings().env == "prod"


def _approved_ip(cfg: dict[str, Any]) -> str | None:
    host = str(cfg.get("host") or "").strip().lower().rstrip(".")
    allowed = {item.strip().lower().rstrip(".") for item in os.environ.get("RENGU_DS_ALLOWED_HOSTS", "").split(",") if item.strip()}
    if host not in allowed:
        return None
    try:
        addresses = socket.getaddrinfo(host, int(cfg["port"]), type=socket.SOCK_STREAM)
        parsed = {ipaddress.ip_address(item[4][0].split("%")[0]) for item in addresses}
        if not parsed or any(
            ip.is_link_local or ip.is_multicast or ip.is_unspecified
            or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped)
            or ip == ipaddress.ip_address("100.100.100.200") for ip in parsed
        ):
            return None
        return str(sorted(parsed, key=lambda ip: (ip.version, int(ip)))[0])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def permitted(kind: str, cfg: dict[str, Any]) -> bool:
    if real_connect_enabled():
        return _approved_ip(cfg) is not None
    if os.environ.get("RENGU_DS_LOCAL_CONNECT") != "1" or kind != "PostgreSQL":
        return False
    try:
        main = make_url(get_settings().database_url)
        return (
            main.drivername == "postgresql+asyncpg"
            and main.host in {"127.0.0.1", "::1", "localhost"}
            and not main.query
            and (cfg.get("host"), cfg.get("port"), cfg.get("database"))
            == (main.host, main.port or 5432, main.database)
        )
    except (ArgumentError, TypeError, ValueError):
        return False


def supports(kind: str) -> bool:
    return kind in DRIVERS


@dataclass
class TableInfo:
    schema: str
    name: str
    comment: str
    cols: int
    rows: int


def _engine(kind: str, cfg: dict[str, Any], *, pooled: bool = False) -> AsyncEngine | Engine:
    host = cfg["host"]
    if not real_connect_enabled() and permitted(kind, cfg) and host == "localhost":
        host = "127.0.0.1"
    driver = DRIVERS[kind]
    if kind == "达梦 DM8":
        from sqlalchemy.dialects import registry
        registry.register("dm.rengu_readonly", "app.modules.data.dm_dialect", "SafeDMDialect")
        driver = "dm+rengu_readonly"
    url = URL.create(
        driver,
        username=cfg.get("user") or None,
        password=cfg.get("password") or None,
        host=host,
        port=int(cfg["port"]),
        # dmPython accepts host/port, not SQLAlchemy's database keyword. DM
        # schemas are selected separately by metadata operations.
        database=None if kind == "达梦 DM8" else cfg.get("database") or None,
    )
    if kind == "达梦 DM8":
        if cfg.get("ssl"):
            raise RuntimeError("达梦驱动的 TLS 证书与主机名校验尚未配置，无法安全建立 TLS 连接")
        if pooled:
            # The CDC worker checks the same source every few hundred milliseconds;
            # reusing its private pool avoids paying a full DM handshake per tick.
            return create_engine(url, connect_args={"connection_timeout": CONNECT_TIMEOUT_S},
                                 pool_size=4, max_overflow=4, pool_recycle=180)
        return create_engine(url, connect_args={"connection_timeout": CONNECT_TIMEOUT_S}, poolclass=NullPool)
    args: dict[str, Any] = (
        {
            "connect_timeout": CONNECT_TIMEOUT_S,
            "ssl": ssl.create_default_context() if cfg.get("ssl") else None,
        }
        if kind == "MySQL"
        else {"timeout": CONNECT_TIMEOUT_S, "ssl": (_PinnedHostnameContext(cfg["_tls_hostname"]) if cfg.get("_tls_hostname") else "verify-full") if cfg.get("ssl") else False}
    )
    return create_async_engine(url, connect_args=args, poolclass=NullPool)


class SourceStageError(RuntimeError):
    def __init__(self, stage: str):
        self.stage = stage
        super().__init__(f"{stage}失败")


async def _read(kind: str, cfg: dict[str, Any], operation, *, stage: str = "读取数据"):
    """达梦官方驱动为同步 DBAPI，在线程中连接和内省；其余使用异步驱动。"""
    if kind == "达梦 DM8" and cfg.get("ssl"):
        raise RuntimeError("达梦 TLS 服务器身份校验尚不可用；请勿将普通连接作为 TLS 使用")
    if real_connect_enabled():
        host = _approved_ip(cfg)
        if host is None:
            raise RuntimeError("未授权连接此数据源")
        if cfg.get("ssl"):
            try:
                ipaddress.ip_address(str(cfg.get("host") or ""))
            except ValueError:
                if kind not in ("PostgreSQL", "人大金仓 KingbaseES"):
                    raise RuntimeError("当前不支持此数据源的域名 TLS 主机名校验") from None
                cfg = {**cfg, "_tls_hostname": str(cfg["host"]).strip().rstrip(".")}
        cfg = {**cfg, "host": host}
    elif not permitted(kind, cfg):
        raise RuntimeError("未授权连接此数据源")

    def sync_read():
        eng = _engine(kind, cfg)
        try:
            try:
                conn = eng.connect()
            except (NoSuchModuleError, ModuleNotFoundError, ImportError):
                raise
            except Exception as error:
                raise SourceStageError("建立连接") from error
            with conn:
                if kind == "达梦 DM8":
                    try:
                        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                    except Exception as error:
                        raise SourceStageError("启用只读事务") from error
                try:
                    return operation(conn)
                except Exception as error:
                    raise SourceStageError(stage) from error
        finally:
            eng.dispose()

    try:
        if kind == "达梦 DM8":
            # shortcut: 线程超时无法中断驱动调用，需驱动/网络超时控制真实连接时长。
            return await asyncio.to_thread(sync_read)
        eng = _engine(kind, cfg)
        try:
            async with eng.connect() as conn:
                return await conn.run_sync(operation)
        finally:
            await eng.dispose()
    except NoSuchModuleError:
        raise RuntimeError(f"{kind} 数据库方言未安装（{DRIVERS[kind]}），未执行连接") from None
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"{kind} 驱动或其依赖未安装（{exc.name or '未知模块'}），未执行连接") from None
    except ImportError:
        raise RuntimeError(f"{kind} 驱动加载失败，请检查驱动及 SQLAlchemy 版本兼容性") from None


def err_text(e: BaseException, password: str | None) -> str:
    """异常第一行，截 200 字；万一带了密码原文就打码。"""
    # Driver exceptions can contain credentials, SQL and business values. Return
    # only a bounded DM error number, never the driver's message or statement.
    candidates = []
    current = e
    seen = set()
    while current is not None and id(current) not in seen and len(seen) < 8:
        seen.add(id(current))
        candidates.append(current)
        original = getattr(current, "orig", None)
        current = original if isinstance(original, BaseException) else current.__cause__ or current.__context__
    phase = next((x.stage for x in candidates if isinstance(x, SourceStageError)), None)
    dm_error = next((x for x in candidates if type(x).__module__.lower().startswith("dmpython")), None)
    if dm_error is not None:
        code = next((str(arg) for arg in dm_error.args if type(arg) is int and -999999 <= arg <= -1), None)
        if code is None:
            match = re.search(r"(?<!\d)-\d{3,6}(?!\d)", str(dm_error))
            code = match.group() if match else None
        return f"达梦{phase or '连接或只读操作'}失败{f'（错误代码 {code}）' if code else '，请检查连接与只读权限'}"
    mysql_error = next((x for x in candidates if type(x).__module__.startswith(('asyncmy', 'pymysql'))), None)
    if mysql_error is not None:
        code = next((arg for arg in mysql_error.args if type(arg) is int and 1000 <= arg <= 4999), None)
        hint = '；请检查源账号SELECT和视图元数据SHOW VIEW权限' if code in (1142, 1143) else ''
        return f"MySQL{phase or '连接或只读操作'}失败{f'（错误代码 {code}）' if code else ''}{hint}"
    if phase:
        return f"{phase}失败，请检查连接与只读权限"
    if isinstance(e, DBAPIError):
        return "数据库连接或只读操作失败，请检查连接与只读权限"
    raw = str(e).strip()
    msg = raw.splitlines()[0] if raw else type(e).__name__
    if password:
        msg = msg.replace(password, "***")
    return msg[:200]


async def test_real(kind: str, cfg: dict[str, Any]) -> ConnectionTestResult:
    if not supports(kind):
        return ConnectionTestResult(False, 0, f"暂不支持真实连接 {kind}（缺驱动）")
    t0 = time.perf_counter()
    try:
        async with asyncio.timeout(CONNECT_TIMEOUT_S):
            await _read(
                kind,
                cfg,
                lambda conn: conn.execute(
                    text("select 1 from dual" if kind == "达梦 DM8" else "select 1")
                ).scalar(),
                stage="连接探测",
            )
        return ConnectionTestResult(True, int((time.perf_counter() - t0) * 1000), "连接成功")
    except TimeoutError:
        return ConnectionTestResult(False, CONNECT_TIMEOUT_S * 1000, f"连接超时（{CONNECT_TIMEOUT_S} 秒）")
    except Exception as e:
        return ConnectionTestResult(
            False, int((time.perf_counter() - t0) * 1000), f"连接失败：{err_text(e, cfg.get('password'))}"
        )


def _list(conn: Connection, schemas: list[str] | None = None, *, names_only: bool = False) -> list[TableInfo]:
    insp = inspect(conn)
    out: list[TableInfo] = []
    available = insp.get_schema_names()
    if schemas is not None and not set(schemas) <= set(available):
        raise ValueError("所选 schema 不存在或当前账号无权查看")
    for schema in schemas if schemas is not None else available:
        if schema.lower() in SYSTEM_SCHEMAS or schema.lower().startswith("pg_"):
            continue
        names = [n for n in insp.get_table_names(schema=schema) if n not in SYSTEM_TABLES]
        views = set()
        if conn.dialect.name == "mysql":
            views = {n for n in insp.get_view_names(schema=schema) if n not in SYSTEM_TABLES}
            names = sorted(set(names) | views)
        readable = readable_table_stats(conn, schema)
        if readable is not None:
            names = [n for n in names if n in readable]
        if not names:
            continue
        cols = ({} if names_only else mysql_catalog_columns(conn, schema, names)
                if conn.dialect.name == "mysql" else insp.get_multi_columns(schema=schema, filter_names=names))
        comment_names = [name for name in names if name not in views]
        comments = {} if names_only or not comment_names else insp.get_multi_table_comment(schema=schema, filter_names=comment_names)
        rows = readable or {}
        for n in sorted(names):
            out.append(
                TableInfo(
                    schema=schema,
                    name=n,
                    comment=((comments.get((schema, n)) or {}).get("text") or "")[:255],
                    cols=-1 if names_only else len(cols.get((schema, n), [])),
                    rows=rows.get(n, -1),
                )
            )
            if names_only and len(out) > MAX_TABLES:
                raise ValueError("所选 schema 表数量超过上限，请缩小选择范围")
            if not names_only and len(out) >= MAX_TABLES:
                return out
    return out


async def list_tables(kind: str, cfg: dict[str, Any], schemas: list[str] | None = None) -> list[TableInfo]:
    """连上源库列出全部用户表。连不上抛原异常（调用方转成页面提示）。"""
    if kind == "达梦 DM8" and not schemas:
        raise ValueError("请先选择需要刷新的 schema")
    require_configured_schema(kind, cfg, schemas or [])
    async with asyncio.timeout(CONNECT_TIMEOUT_S * 3):
        return await _read(kind, cfg, lambda conn: _list(conn, schemas, names_only=kind == "达梦 DM8"), stage="读取元数据")


async def list_schemas(kind: str, cfg: dict[str, Any]) -> list[str]:
    async with asyncio.timeout(CONNECT_TIMEOUT_S * 3):
        names = await _read(kind, cfg, lambda conn: inspect(conn).get_schema_names(), stage="读取元数据")
    if len(names) > MAX_TABLES:
        raise ValueError("可见 schema 数量超过上限")
    names = sorted(name for name in names if name.lower() not in SYSTEM_SCHEMAS and not name.lower().startswith("pg_"))
    expected = str(cfg.get("database") or "").strip() if kind == "达梦 DM8" else ""
    if expected:
        if expected not in names:
            raise ValueError("配置的 schema 不存在或当前账号无权查看；名称区分大小写")
        return [expected]
    return names


def require_configured_schema(kind: str, cfg: dict[str, Any], schemas: list[str]) -> None:
    expected = str(cfg.get("database") or "").strip() if kind == "达梦 DM8" else ""
    if expected and any(schema != expected for schema in schemas):
        raise ValueError("所选 schema 与数据源限定范围不一致；名称区分大小写")


async def inspect_tables(kind: str, cfg: dict[str, Any], schema: str, tables: list[str]) -> list[Table]:
    """连接已保存数据源，读取选中表的列/主外键；不执行数据查询或写入。"""
    require_configured_schema(kind, cfg, [schema])
    async with asyncio.timeout(CONNECT_TIMEOUT_S * 3):
        return await _read(kind, cfg, lambda conn: inspect_connection_tables(conn, schema, tables), stage="读取元数据")


def assert_unique_key(conn, schema: str, name: str, key: str):
    """A view declaration is not a constraint: recheck NULL and duplicate keys in the read transaction."""
    from sqlalchemy import column, func, select, table
    if not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value) for value in (schema, name, key)):
        raise ValueError("视图唯一键或来源名称无效")
    source = table(name, column(key), schema=schema)
    field = source.c[key]
    if conn.execute(select(field).where(field.is_(None)).limit(1)).first() is not None:
        raise ValueError("视图声明唯一键包含空值，未发布镜像")
    if conn.execute(select(field).group_by(field).having(func.count() > 1).limit(1)).first() is not None:
        raise ValueError("视图声明唯一键存在重复，未发布镜像")


async def validate_view_key(kind, cfg, schema, name, key):
    require_configured_schema(kind, cfg, [schema])
    try:
        return await _read(kind, cfg, lambda conn: assert_unique_key(conn, schema, name, key), stage="验证视图唯一键")
    except ValueError as exc:
        from app.core.errors import AppError
        raise AppError(str(exc), code="VIEW_KEY_NOT_UNIQUE", status_code=422) from None
