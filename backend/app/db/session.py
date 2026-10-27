"""异步引擎与会话。

- 请求内用依赖 `SessionDep` 拿会话；一个请求一个事务，正常返回时提交，抛错时回滚。
  依赖用 `scope="function"`：提交在**发送响应之前**完成，提交失败 → 500（不会先回 200 再丢数据）。
- 服务层不要自己 commit（跨模块的契约，例如发布时 create_version，要求在调用方同一个事务里完成）。
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.common import ontology as _ontology  # noqa: F401
from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_qa_engine: AsyncEngine | None = None


class QaReadonlyNotConfigured(RuntimeError):
    """没配 RENGU_QA_READONLY_DATABASE_URL。问数网关不回退到主库。"""


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.database_url, echo=s.db_echo, pool_size=s.db_pool_size, pool_pre_ping=True
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


def qa_readonly_engine_kwargs(statement_timeout_ms: int) -> dict:
    """连接建立时（asyncpg server_settings，即 startup 参数）设置，会话里改不回只读以外的默认值。"""
    return {
        "connect_args": {
            "server_settings": {
                "statement_timeout": str(int(statement_timeout_ms)),
                "default_transaction_read_only": "on",
            }
        },
        "pool_pre_ping": True,
    }


def get_qa_readonly_engine() -> AsyncEngine:
    """问数网关专用只读引擎（app/common/sql_guard.run_guarded 用它的连接）。

    未配置 `qa_readonly_database_url` 直接抛 QaReadonlyNotConfigured，**不回退到 database_url**。
    每个连接：statement_timeout = qa_statement_timeout_ms，default_transaction_read_only = on。
    库账号本身也应只读、只授权业务表（运维配置），这里是第二道闸。
    """
    global _qa_engine
    if _qa_engine is None:
        s = get_settings()
        if not s.qa_readonly_database_url:
            raise QaReadonlyNotConfigured("未配置 RENGU_QA_READONLY_DATABASE_URL，问数网关拒绝执行")
        _qa_engine = create_async_engine(
            s.qa_readonly_database_url, **qa_readonly_engine_kwargs(s.qa_statement_timeout_ms)
        )
    return _qa_engine


async def dispose_engine() -> None:
    global _engine, _sessionmaker, _qa_engine
    if _engine is not None:
        await _engine.dispose()
    if _qa_engine is not None:
        await _qa_engine.dispose()
    _engine, _sessionmaker, _qa_engine = None, None, None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
