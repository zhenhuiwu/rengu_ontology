"""完整 seed 端到端：全新空库 → alembic upgrade head → `python -m app.cli seed`（全部阶段、当前模块）。

QA 跑骨架时发现：还没有模块登记快照 section 时，versioning 阶段报「没有任何快照 section 登记」，
整个 seed 失败。现在应跳过版本记录、其余阶段照常写入，并且可以重复跑（幂等）。
"""

import asyncio
import os
import subprocess
import sys
import uuid

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from tests.conftest import BASE_URL


def _admin_dsn() -> str:
    return make_url(BASE_URL).set(drivername="postgresql").render_as_string(hide_password=False)


async def _count(dsn: str, table: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(f'SELECT count(*) FROM public."{table}"')
    finally:
        await conn.close()


async def test_full_seed_on_fresh_db():
    db = f"rengu_seed_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(_admin_dsn())
    await admin.execute(f'CREATE DATABASE "{db}"')
    url = make_url(BASE_URL).set(database=db).render_as_string(hide_password=False)
    plain = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        cfg = Config("alembic.ini")
        cfg.attributes["database_url"] = url
        await asyncio.to_thread(command.upgrade, cfg, "head")
        env = {**os.environ, "RENGU_DATABASE_URL": url, "RENGU_ENV": "test"}

        def seed() -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, "-m", "app.cli", "seed"],
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )

        for _ in range(2):  # 第二遍验证幂等
            r = await asyncio.to_thread(seed)
            assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-4000:]
            assert "完成：" in r.stdout
        assert await _count(plain, "sys_user") > 0
        assert await _count(plain, "biz_order") > 0
        assert await _count(plain, "ontology_release") == 0  # 没有 section：版本记录跳过，不写空快照
    finally:
        await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", db)
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await admin.close()
