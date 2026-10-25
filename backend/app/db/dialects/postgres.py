import zlib
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
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
