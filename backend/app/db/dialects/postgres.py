import zlib
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


class PostgresAdapter:
    name = "postgresql"

    async def upsert(
        self,
        session: AsyncSession,
        model: type[DeclarativeBase],
        rows: Sequence[dict[str, Any]],
        key: Sequence[str],
    ) -> int:
        if not rows:
            return 0
        stmt = insert(model).values(list(rows))
        update_cols = {c: stmt.excluded[c] for c in rows[0] if c not in key}
        stmt = (
            stmt.on_conflict_do_update(index_elements=list(key), set_=update_cols)
            if update_cols
            else stmt.on_conflict_do_nothing(index_elements=list(key))
        )
        await session.execute(stmt)
        return len(rows)

    async def xact_lock(self, session: AsyncSession, name: str) -> None:
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": zlib.crc32(name.encode())})
