"""测试基建。

- 使用独立库 rengu_test（不存在会自动创建），会话开始时跑一遍 `alembic upgrade head`，顺便验证迁移；
- 每个测试在一个外层事务里跑，结束整体回滚，互不影响；应用里的 commit 只会释放 savepoint。
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy.engine import make_url

BASE_URL = os.environ.get("RENGU_DATABASE_URL", "postgresql+asyncpg://rengu:rengu@127.0.0.1:5440/rengu")
TEST_URL = make_url(BASE_URL).set(database=os.environ.get("RENGU_TEST_DB", "rengu_test"))
os.environ["RENGU_DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)
os.environ["RENGU_ENV"] = "test"
os.environ.setdefault("RENGU_FIXED_NOW", "2026-10-25T15:42:00+08:00")
# 本地 .env 的真实密钥不能隐式启用测试请求；实连需在启动测试时显式传入。
os.environ.setdefault("DASHSCOPE_API_KEY", "")

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

# 在 autouse 的 _isolated_sections 之前导入 app：create_app() 会调 load_sections() 登记真实 builder。
# 若首次导入发生在某个测试的 client fixture 里（隔离注册表内），真实 objectTypes 会占位，
# 随后 sections fixture 登记 FakeObjectTypesSection 就报「已经登记过」（单独跑某个测试文件时复现）。
import app.main  # noqa: E402, F401


@pytest.fixture(scope="session")
def event_loop_policy():
    # psycopg 的异步队列连接在 Windows 需要 Selector；整个测试会话共用同一循环。
    return (
        asyncio.WindowsSelectorEventLoopPolicy()
        if sys.platform == "win32"
        else asyncio.DefaultEventLoopPolicy()
    )


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
        # 每个请求一个 SAVEPOINT：422 / 业务错误只回滚这一次请求的写入，
        # 不把 seeded / users 等外层夹具的数据一起清掉（否则同一 client 后续请求会 401）。
        async with session.begin_nested():
            yield session

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


@pytest.fixture(autouse=True)
def _isolated_sections():
    """每个测试从空的快照 section 注册表开始，结束后恢复（业务模块在导入时登记的真实 builder 不受影响）。

    用公开的 isolated_sections()，不要直接改 sections._registry。
    """
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections():
        yield


@pytest.fixture
def sections(_isolated_sections):  # 依赖隔离 fixture：保证先清空再登记
    from app.modules.versioning.sections import register_section, unregister_section

    b = FakeObjectTypesSection()
    register_section(b)
    yield b
    unregister_section(b.section)


@pytest.fixture
async def seeded(session, sections) -> AsyncSession:
    from app.seeds.loader import run_seeds

    await run_seeds(session, ["admin", "versioning"])
    await session.flush()
    return session


@pytest.fixture
async def users(session) -> AsyncSession:
    """只灌 sys_user（X-Demo-As / 发布人按用户编号解析要用）。"""
    from app.seeds.loader import run_seeds

    await run_seeds(session, ["admin"])
    await session.flush()
    return session
