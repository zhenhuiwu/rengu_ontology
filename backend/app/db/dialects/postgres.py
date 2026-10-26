import zlib
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import Text, case, cast, column, func, literal, text
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import DeclarativeBase

PG_MAX_PARAMS = 32767


def batch_size(ncols: int) -> int:
    return max(1, PG_MAX_PARAMS // max(1, ncols))


def chunked(rows: Sequence[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [list(rows[i : i + size]) for i in range(0, len(rows), size)]


def dedupe_by_key(rows: Sequence[dict[str, Any]], key: Sequence[str]) -> list[dict[str, Any]]:
    seen: dict[tuple, dict[str, Any]] = {}
    for r in rows:
        seen[tuple(r[k] for k in key)] = r
    return list(seen.values())


class PostgresAdapter:
    name = "postgresql"

    def json_array_rows(self, expression: Any, *, as_text: bool = False) -> Any:
        value = cast(expression, JSONB)
        safe = case((func.jsonb_typeof(value) == "array", value), else_=cast(literal("[]"), JSONB))
        expand = func.jsonb_array_elements_text if as_text else func.jsonb_array_elements
        return (
            expand(safe)
            .table_valued(column("value", Text if as_text else JSONB), with_ordinality="ordinality")
            .render_derived()
            .lateral()
        )

    def json_object(self, **values: Any) -> Any:
        pairs = [item for key, value in values.items() for item in (key, value)]
        return func.jsonb_build_object(*pairs, type_=JSONB)

    def json_array(self, *values: Any) -> Any:
        return func.jsonb_build_array(*values, type_=JSONB)

    def text_aggregate(self, value: Any, separator: str, order: Any) -> Any:
        return func.string_agg(value, aggregate_order_by(literal(separator), *order))

    def text_matches(self, value: Any, pattern: str) -> Any:
        return value.op("~")(pattern)

    def json_merge(self, *values: Any) -> Any:
        def safe(value):
            value = cast(value, JSONB)
            return case((func.jsonb_typeof(value) == "object", value), else_=cast(literal("{}"), JSONB))

        result = safe(values[0])
        for value in values[1:]:
            result = result.op("||")(safe(value))
        return result

    async def upsert(
        self,
        session: AsyncSession,
        model: type[DeclarativeBase],
        rows: Sequence[dict[str, Any]],
        key: Sequence[str],
    ) -> int:
        """分批：每批行数 = PG_MAX_PARAMS // 列数（asyncpg 一条语句最多 32767 个绑定参数）；
        批内按 key 去重（同一条 INSERT … ON CONFLICT 里同一个键出现两次会报错），后出现的覆盖先出现的。"""
        rows = dedupe_by_key(rows, key)
        if not rows:
            return 0
        ncols = max(len(rows[0]), len(model.__table__.columns))  # 默认值列也会成为绑定参数
        for chunk in chunked(rows, batch_size(ncols)):
            stmt = insert(model).values(chunk)
            update_cols = {c: stmt.excluded[c] for c in chunk[0] if c not in key}
            stmt = (
                stmt.on_conflict_do_update(index_elements=list(key), set_=update_cols)
                if update_cols
                else stmt.on_conflict_do_nothing(index_elements=list(key))
            )
            await session.execute(stmt)
        return len(rows)

    async def xact_lock(self, session: AsyncSession, name: str) -> None:
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": zlib.crc32(name.encode())})

    async def try_xact_lock(self, session: AsyncSession, name: str) -> bool:
        return bool(
            await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": zlib.crc32(name.encode())}
            )
        )

    @asynccontextmanager
    async def session_lock(self, connection: AsyncConnection, name: str) -> AsyncIterator[None]:
        params = {"k": zlib.crc32(name.encode())}
        try:
            await connection.execute(text("SELECT pg_advisory_lock(:k)"), params)
            await connection.commit()
            yield
        finally:
            try:
                await connection.rollback()
                await connection.execute(text("SELECT pg_advisory_unlock(:k)"), params)
                await connection.commit()
            except BaseException:
                await connection.invalidate()  # 解锁失败的连接不能带着会话锁回池。
                raise
