from collections.abc import Sequence
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


class DialectAdapter(Protocol):
    name: str

    async def upsert(
        self,
        session: AsyncSession,
        model: type[DeclarativeBase],
        rows: Sequence[dict[str, Any]],
        key: Sequence[str],
    ) -> int:
        """按 key 插入或更新（seed、外部同步用）。返回处理行数。"""
        ...

    async def xact_lock(self, session: AsyncSession, name: str) -> None:
        """当前事务内的全局互斥锁（例如“同一时刻只允许一次整版发布”），事务结束自动释放。"""
        ...
