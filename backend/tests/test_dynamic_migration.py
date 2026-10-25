"""dynamic_0001 单独升降：不假定它是 head（合并后别的模块会排在它前后），
只升到 dynamic_0001、降回它的 down_revision，只比较 dynamic 自己的四张表。
"""

import asyncio
import uuid

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from tests.conftest import BASE_URL

REV = "dynamic_0001"
OWN_TABLES = {"alert", "alert_log", "action_run", "action_run_log"}


async def test_dynamic_0001_up_down_up_own_tables_only():
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    down = script.get_revision(REV).down_revision
    assert isinstance(down, str), down  # 线性：只有一个前驱

    db = f"rengu_dynmig_{uuid.uuid4().hex[:8]}"
    admin_url = make_url(BASE_URL).set(drivername="postgresql").render_as_string(hide_password=False)
    plain = make_url(BASE_URL).set(drivername="postgresql", database=db).render_as_string(hide_password=False)
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{db}"')
    try:
        cfg = Config("alembic.ini")
        cfg.attributes["database_url"] = (
            make_url(BASE_URL).set(database=db).render_as_string(hide_password=False)
        )

        async def snapshot() -> tuple[set[str], set[str], str | None]:
            conn = await asyncpg.connect(plain)
            try:
                tables = {
                    r["tablename"]
                    for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                }
                types = {
                    r["typname"]
                    for r in await conn.fetch(
                        "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace"
                        " WHERE n.nspname = 'public' AND t.typtype IN ('e', 'd')"
                    )
                }
                ver = (
                    await conn.fetchval("SELECT version_num FROM alembic_version")
                    if "alembic_version" in tables
                    else None
                )
                return tables, types, ver
            finally:
                await conn.close()

        await asyncio.to_thread(command.upgrade, cfg, down)
        before_tables, before_types, ver = await snapshot()
        assert ver == down and not (before_tables & OWN_TABLES)

        await asyncio.to_thread(command.upgrade, cfg, REV)
        tables, types, ver = await snapshot()
        assert ver == REV
        assert tables - before_tables == OWN_TABLES  # 只建自己的表
        assert types == before_types  # 不建 PG 原生 enum / domain

        await asyncio.to_thread(command.downgrade, cfg, down)
        assert await snapshot() == (before_tables, before_types, down)  # 降回去只删自己的表

        await asyncio.to_thread(command.upgrade, cfg, REV)
        assert (await snapshot())[0] == tables
    finally:
        await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", db)
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await admin.close()
