"""完整 seed 端到端：全新空库 → alembic upgrade head → `python -m app.cli seed`（全部阶段、当前模块）。

QA 跑骨架时发现：还没有模块登记快照 section 时，versioning 阶段报「没有任何快照 section 登记」，
整个 seed 失败。现在应跳过版本记录、其余阶段照常写入，并且可以重复跑（幂等）。
模块登记了 section（app/modules/<模块>/sections.py，load_sections 自动发现）后，版本记录照常写入。
静态模块登记 section 之后（static_0001），seed 会写出 mock 的 5 个历史版本。
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from tests.conftest import BASE_URL

RELEASES = json.loads((get_settings().seeds_json_dir / "static" / "RELEASES.json").read_text("utf-8"))


def _admin_dsn() -> str:
    return make_url(BASE_URL).set(drivername="postgresql").render_as_string(hide_password=False)


async def _count(dsn: str, table: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(f'SELECT count(*) FROM public."{table}"')
    finally:
        await conn.close()


async def _seed_fresh_db(extra_env: dict[str, str], check) -> None:
    db = f"rengu_seed_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(_admin_dsn())
    await admin.execute(f'CREATE DATABASE "{db}"')
    url = make_url(BASE_URL).set(database=db).render_as_string(hide_password=False)
    plain = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    try:
        cfg = Config("alembic.ini")
        cfg.attributes["database_url"] = url
        await asyncio.to_thread(command.upgrade, cfg, "head")
        env = {**os.environ, "RENGU_DATABASE_URL": url, "RENGU_ENV": "test", **extra_env}

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
        await check(plain)
    finally:
        await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", db)
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await admin.close()


async def test_full_seed_on_fresh_db():
    """当前分支默认 seed（只扫 app.modules）。骨架上没有模块导出 section：版本记录跳过，不写空快照。"""
    from app.modules.versioning.sections import isolated_sections, load_sections

    with isolated_sections():
        found = load_sections(["app.modules"])  # 本分支实际能发现的 section

    async def check(plain):
        n = await _count(plain, "ontology_release")
        if not found:
            assert n == 0
            return
        assert n == len(RELEASES)
        assert set(found) <= await _snapshot_sections(plain)  # 验收：默认 seed 的版本里有你的 section

    await _seed_fresh_db({}, check)


async def test_default_seed_includes_discovered_sections():
    """验收口径：模块把 builder 放进 app/modules/<模块>/sections.py → 新库默认 seed 的每个版本快照里都有它。

    这里用测试替身包 tests/fixtures/section_mods（alpha 导出 BUILDERS、beta 导出 register()）模拟模块；
    替身用 `_test_` 前缀的 section 名，和真实模块（静态的 terms 等）一起扫也不冲突。
    """
    from tests.fixtures.section_mods import ALPHA, BETA

    # 只扫替身包：真实模块（app.modules）的发现交给 test_full_seed_on_fresh_db，这里不受各模块合并进度影响
    pkgs = '["tests.fixtures.section_mods"]'

    async def check(plain):
        assert await _count(plain, "ontology_release") == len(RELEASES)
        conn = await asyncpg.connect(plain)
        try:
            rows = await conn.fetch("SELECT ver, snapshot FROM ontology_release ORDER BY id")
        finally:
            await conn.close()
        for r in rows:
            snap = json.loads(r["snapshot"])
            # 不断言「只有这两节」：load_sections() 也会经 BUILDERS / register() 发现真实模块的 section；
            # 替身名用 _test_ 前缀，所以并存也不冲突。
            assert {ALPHA, BETA} <= set(snap)
            assert snap[ALPHA] == {"probe": {"since": r["ver"]}}
            assert snap[BETA] == {"probe_event": {"ver": r["ver"]}}

    await _seed_fresh_db({"RENGU_SECTION_PACKAGES": pkgs}, check)


async def _snapshot_sections(plain: str) -> set[str]:
    conn = await asyncpg.connect(plain)
    try:
        rows = await conn.fetch("SELECT snapshot FROM ontology_release")
    finally:
        await conn.close()
    return set.intersection(*(set(json.loads(r["snapshot"])) for r in rows))
