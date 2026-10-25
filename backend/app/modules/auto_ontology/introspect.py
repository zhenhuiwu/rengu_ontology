"""表结构内省（SQLAlchemy inspect，方言无关；达梦 / 金仓走各自的 SQLAlchemy 方言即可复用）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect, types
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError

# 迁移 / 框架自身的表，不进本体
SYSTEM_TABLES = frozenset(
    {
        "alembic_version",
        "procrastinate_jobs",
        "procrastinate_events",
        "procrastinate_periodic_defers",
        "procrastinate_workers",
    }
)
MAX_TABLES = 60


class SchemaNotFound(AppError):
    status_code, code, message = 404, "SCHEMA_NOT_FOUND", "数据源里没有这个 schema"


class TablesNotFound(AppError):
    status_code, code, message = 404, "TABLES_NOT_FOUND", "数据源里没有这些表"


class TooManyTables(AppError):
    status_code, code, message = 422, "TOO_MANY_TABLES", f"一次最多 {MAX_TABLES} 张表，请用 tables 指定"


@dataclass
class Column:
    name: str
    sql_type: str
    kind: str  # 本体属性类型：string / integer / decimal / boolean / date / timestamp / enum
    nullable: bool
    comment: str | None = None


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    name: str
    comment: str | None
    columns: list[Column]
    pk: list[str]
    fks: list[ForeignKey] = field(default_factory=list)
    unique_sets: list[list[str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "comment": self.comment,
            "pk": self.pk,
            "columns": [
                {"name": c.name, "type": c.sql_type, "nullable": c.nullable, "comment": c.comment}
                for c in self.columns
            ],
            "fks": [
                {"columns": f.columns, "refTable": f.ref_table, "refColumns": f.ref_columns} for f in self.fks
            ],
        }


def column_kind(t: types.TypeEngine[Any]) -> str:
    """列类型 → 本体属性类型（前端 objectProps.ts 用到的 7 种）。"""
    if isinstance(t, types.Boolean):
        return "boolean"
    if isinstance(t, types.Enum):
        return "enum"
    if isinstance(t, types.Integer):
        return "integer"
    if isinstance(t, types.Numeric):  # Float 是 Numeric 子类
        return "decimal"
    if isinstance(t, types.DateTime):
        return "timestamp"
    if isinstance(t, types.Date):
        return "date"
    return "string"


def _inspect(conn: Connection, schema: str, only: list[str] | None) -> list[Table]:
    insp = inspect(conn)
    if schema not in insp.get_schema_names():
        raise SchemaNotFound(details={"schema": schema})
    names = [n for n in insp.get_table_names(schema=schema) if n not in SYSTEM_TABLES]
    if only:
        missing = sorted(set(only) - set(names))
        if missing:
            raise TablesNotFound(details={"schema": schema, "tables": missing})
        names = [n for n in names if n in set(only)]
    if len(names) > MAX_TABLES:
        raise TooManyTables(details={"count": len(names), "max": MAX_TABLES})
    out: list[Table] = []
    for name in sorted(names):
        cols = [
            Column(c["name"], str(c["type"]), column_kind(c["type"]), bool(c["nullable"]), c.get("comment"))
            for c in insp.get_columns(name, schema=schema)
        ]
        pk = list(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
        fks = [
            ForeignKey(list(f["constrained_columns"]), f["referred_table"], list(f["referred_columns"]))
            for f in insp.get_foreign_keys(name, schema=schema)
            if f.get("referred_table") and (f.get("referred_schema") in (None, schema))
        ]
        uniq = [list(u["column_names"]) for u in insp.get_unique_constraints(name, schema=schema)]
        uniq += [
            list(i["column_names"])
            for i in insp.get_indexes(name, schema=schema)
            if i.get("unique") and all(i["column_names"])
        ]
        comment = (insp.get_table_comment(name, schema=schema) or {}).get("text")
        out.append(Table(name, comment, cols, pk, fks, uniq))
    return out


async def introspect(session: AsyncSession, schema: str, tables: list[str] | None = None) -> list[Table]:
    """读应用自己的库（当前会话连接）。EXTENSION POINT (data)：数据连接模块就绪后按 datasource 取连接。"""
    conn = await session.connection()
    return await conn.run_sync(_inspect, schema, tables)
