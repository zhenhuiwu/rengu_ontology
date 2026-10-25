"""测试基建。

- 使用独立库 rengu_test（不存在会自动创建），会话开始时跑一遍 `alembic upgrade head`，顺便验证迁移；
- 每个测试在一个外层事务里跑，结束整体回滚，互不影响；应用里的 commit 只会释放 savepoint。
"""

import asyncio
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy.engine import make_url

BASE_URL = os.environ.get("RENGU_DATABASE_URL", "postgresql+asyncpg://rengu:rengu@127.0.0.1:5440/rengu")
TEST_URL = make_url(BASE_URL).set(database=os.environ.get("RENGU_TEST_DB", "rengu_test"))
os.environ["RENGU_DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)
os.environ["RENGU_ENV"] = "test"
os.environ.setdefault("RENGU_FIXED_NOW", "2026-10-25T15:42:00+08:00")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


async def _ensure_db() -> None:
    admin = make_url(BASE_URL).set(drivername="postgresql")
    conn = await asyncpg.connect(admin.render_as_string(hide_password=False))
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", TEST_URL.database):
            await conn.execute(f'CREATE DATABASE "{TEST_URL.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
async def engine():
    await _ensure_db()
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = os.environ["RENGU_DATABASE_URL"]
    await asyncio.to_thread(command.upgrade, cfg, "head")
    eng = create_async_engine(os.environ["RENGU_DATABASE_URL"])
    yield eng
    await eng.dispose()


@pytest.fixture
async def conn(engine) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as c:
        trans = await c.begin()
        yield c
        await trans.rollback()


@pytest.fixture
async def session(conn) -> AsyncIterator[AsyncSession]:
    s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield s
    await s.close()


@pytest.fixture
async def client(session) -> AsyncIterator[AsyncClient]:
    from app.db.session import get_session
    from app.main import app

    async def _override():
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class FakeObjectTypesSection:
    """测试替身：模拟静态登记的 objectTypes section（真实实现归静态模块）。"""

    section = "objectTypes"

    async def build_section(self, session, base, change_order_ids):
        return {**(base or {}), "order": {"label": "订单", "applied": change_order_ids}}

    def seed_section(self, ver, ctx):
        return {"order": {"label": "订单", "since": ver}}


@pytest.fixture
def sections():
    from app.modules.versioning.sections import register_section, unregister_section

    b = FakeObjectTypesSection()
    register_section(b)
    yield b
    unregister_section(b.section)


@pytest.fixture
async def seeded(session, sections) -> AsyncSession:
    from app.seeds.loader import run_seeds

    await run_seeds(session, ["versioning"])
    await session.flush()
    return session
