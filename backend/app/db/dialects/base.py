from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.orm import DeclarativeBase


class DialectAdapter(Protocol):
    name: str

    def json_array_rows(self, expression: Any, *, as_text: bool = False) -> Any:
        """安全展开JSON数组为value/ordinality关系；null或非数组视为空，返回SQLAlchemy表达式。"""
        ...

    def json_object(self, **values: Any) -> Any: ...

    def json_array(self, *values: Any) -> Any: ...

    def text_aggregate(self, value: Any, separator: str, order: Any) -> Any: ...

    def text_matches(self, value: Any, pattern: str) -> Any: ...

    def json_merge(self, *values: Any) -> Any:
        """只合并JSON对象，null/非对象视为空对象，用户值始终由绑定参数传入。"""
        ...

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

    async def try_xact_lock(self, session: AsyncSession, name: str) -> bool:
        """尝试获取事务锁；已由其他事务持有时立即返回 False。"""
        ...

    def session_lock(self, connection: AsyncConnection, name: str) -> AbstractAsyncContextManager[None]:
        """独占显式连接上的会话锁，允许业务逐次提交；退出时释放后才能归还连接池。"""
        ...
