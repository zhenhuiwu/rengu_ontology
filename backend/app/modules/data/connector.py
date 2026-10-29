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
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, NoSuchModuleError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.modules.auto_ontology.introspect import SYSTEM_TABLES, Table
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


def _engine(kind: str, cfg: dict[str, Any]) -> AsyncEngine | Engine:
    host = cfg["host"]
    if not real_connect_enabled() and permitted(kind, cfg) and host == "localhost":
        host = "127.0.0.1"
    url = URL.create(
        DRIVERS[kind],
        username=cfg.get("user") or None,
        password=cfg.get("password") or None,
        host=host,
        port=int(cfg["port"]),
        database=cfg.get("database") or None,
    )
    if kind == "达梦 DM8":
        if cfg.get("ssl"):
            raise RuntimeError("达梦驱动的 TLS 证书与主机名校验尚未配置，无法安全建立 TLS 连接")
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


async def _read(kind: str, cfg: dict[str, Any], operation):
    """达梦官方驱动为同步 DBAPI，在线程中连接和内省；其余使用异步驱动。"""
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
            with eng.connect() as conn:
                return operation(conn)
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
            )
        return ConnectionTestResult(True, int((time.perf_counter() - t0) * 1000), "连接成功")
    except TimeoutError:
        return ConnectionTestResult(False, CONNECT_TIMEOUT_S * 1000, f"连接超时（{CONNECT_TIMEOUT_S} 秒）")
    except Exception as e:
        return ConnectionTestResult(
            False, int((time.perf_counter() - t0) * 1000), f"连接失败：{err_text(e, cfg.get('password'))}"
        )


def _list(conn: Connection) -> list[TableInfo]:
    insp = inspect(conn)
    out: list[TableInfo] = []
    for schema in insp.get_schema_names():
        if schema in SYSTEM_SCHEMAS or schema.startswith("pg_"):
            continue
        names = [n for n in insp.get_table_names(schema=schema) if n not in SYSTEM_TABLES]
        if not names:
            continue
        cols = insp.get_multi_columns(schema=schema, filter_names=names)
        comments = insp.get_multi_table_comment(schema=schema, filter_names=names)
        rows = (
            {
                r[0]: int(r[1]) if r[1] is not None and r[1] >= 0 else -1
                for r in conn.execute(
                    text(
                        "select c.relname, c.reltuples from pg_class c"
                        " join pg_namespace n on n.oid = c.relnamespace"
                        " where n.nspname = :s and c.relkind in ('r','p')"
                    ),
                    {"s": schema},
                )
            }
            if conn.dialect.name == "postgresql"
            else {}
        )
        for n in sorted(names):
            out.append(
                TableInfo(
                    schema=schema,
                    name=n,
                    comment=((comments.get((schema, n)) or {}).get("text") or "")[:255],
                    cols=len(cols.get((schema, n), [])),
                    rows=rows.get(n, -1),
                )
            )
            if len(out) >= MAX_TABLES:
                return out
    return out


async def list_tables(kind: str, cfg: dict[str, Any]) -> list[TableInfo]:
    """连上源库列出全部用户表。连不上抛原异常（调用方转成页面提示）。"""
    async with asyncio.timeout(CONNECT_TIMEOUT_S * 3):
        return await _read(kind, cfg, _list)


async def inspect_tables(kind: str, cfg: dict[str, Any], schema: str, tables: list[str]) -> list[Table]:
    """连接已保存数据源，读取选中表的列/主外键；不执行数据查询或写入。"""
    async with asyncio.timeout(CONNECT_TIMEOUT_S * 3):
        return await _read(kind, cfg, lambda conn: inspect_connection_tables(conn, schema, tables))
