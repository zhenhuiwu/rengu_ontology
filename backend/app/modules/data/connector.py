"""真实连接数据源：测试连接 + 库表内省（数据连接页「测试连接」和「库表」页签）。

什么时候真连：`RENGU_DS_REAL_CONNECT=1`，或 env = prod。否则服务明确返回未开启，不模拟成功。

配置 PostgreSQL/金仓 PG 协议、MySQL 与达梦驱动；可选驱动未安装时明确失败，不模拟成功。

内省复用 auto_ontology 的思路（SQLAlchemy inspect，方言无关），但只取清单要的东西：
schema、表名、表注释、字段数、估算行数（PG 用 pg_class.reltuples，不 count(*)）。
"""

from __future__ import annotations

import asyncio
import os
import ssl
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import NoSuchModuleError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.modules.auto_ontology.introspect import SYSTEM_TABLES, Table
from app.modules.auto_ontology.introspect import inspect_tables as inspect_connection_tables
from app.modules.data.adapters import ConnectionTestResult

CONNECT_TIMEOUT_S = 10
MAX_TABLES = 2000
SYSTEM_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast", "sys", "sys_catalog"})

# 类型（中文名，DsType.value）→ SQLAlchemy 异步驱动
DRIVERS: dict[str, str] = {
    "PostgreSQL": "postgresql+asyncpg",
    "人大金仓 KingbaseES": "postgresql+asyncpg",
    "MySQL": "mysql+asyncmy",
    "达梦 DM8": "dm+dmPython",
}


def real_connect_enabled() -> bool:
    return os.environ.get("RENGU_DS_REAL_CONNECT") == "1" or get_settings().env == "prod"


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
    url = URL.create(
        DRIVERS[kind],
        username=cfg.get("user") or None,
        password=cfg.get("password") or None,
        host=cfg["host"],
        port=int(cfg["port"]),
        database=cfg.get("database") or None,
    )
    if kind == "达梦 DM8":
        return create_engine(url, poolclass=NullPool)
    args: dict[str, Any] = (
        {
            "connect_timeout": CONNECT_TIMEOUT_S,
            "ssl": ssl.create_default_context() if cfg.get("ssl") else None,
        }
        if kind == "MySQL"
        else {"timeout": CONNECT_TIMEOUT_S, "ssl": "prefer" if cfg.get("ssl") else False}
    )
    return create_async_engine(url, connect_args=args, poolclass=NullPool)


async def _read(kind: str, cfg: dict[str, Any], operation):
    """达梦官方驱动为同步 DBAPI，在线程中连接和内省；其余使用异步驱动。"""

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
    except (ImportError, NoSuchModuleError):
        raise RuntimeError(f"{kind} 驱动未安装（{DRIVERS[kind]}），未执行连接") from None


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
                r[0]: max(int(r[1] or 0), 0)
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
                    rows=rows.get(n, 0),
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
