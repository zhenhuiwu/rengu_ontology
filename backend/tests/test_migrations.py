"""迁移约定（CONVENTIONS §16）：线性单 head、模块 revision 命名、
升 → 降到 base（不留表和自定义类型）→ 再升。
"""

import asyncio
import re
import uuid
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from tests.conftest import BASE_URL
from tests.helpers.migrations import db_state, skip_forward_only_downgrade, temp_database

SKELETON = {"0001", "0002", "0003"}
# 既有子功能/跨模块revision归属；保留已应用的revision，不为目录名重写迁移历史。
REVISION_OWNERS = {
    "data_quality_0006": ("data",),
    "quality_cr_0011": ("data",),
    "palantir_0007": ("action", "event"),
    "notify_0022": ("notifications",),
    "notify_0028": ("notifications",),
    "quality_0020": ("data",),
}
BARE_FILENAMES = {"data_quality_0006.py", "functions_0008.py", "quality_cr_0011.py"}

# public 下用户定义的类型：enum(e) / 复合(c，只算 CREATE TYPE … AS，不算表的行类型) /
# domain(d) / range(r, m)。
# 不算数组类型（某类型的 typarray）和扩展自带的类型（如 vector，扩展不随 downgrade 删）。
CUSTOM_TYPES_SQL = """
SELECT t.typname, t.typtype::text AS typtype
FROM pg_type t
JOIN pg_namespace n ON n.oid = t.typnamespace
LEFT JOIN pg_class c ON c.oid = t.typrelid
WHERE n.nspname = 'public'
  AND t.typtype IN ('e', 'c', 'd', 'r', 'm')
  AND (t.typtype <> 'c' OR c.relkind = 'c')
  AND NOT EXISTS (SELECT 1 FROM pg_type el WHERE el.typarray = t.oid)
  AND NOT EXISTS (
    SELECT 1 FROM pg_depend d
    WHERE d.classid = 'pg_type'::regclass AND d.objid = t.oid AND d.deptype = 'e'
  )
"""
MODULE_REV = re.compile(r"^[a-z_]+_\d{4}$")


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def test_single_head_and_module_revision_names():
    script = _script()
    assert len(script.get_heads()) == 1, script.get_heads()
    revs = list(script.walk_revisions())
    assert {r.revision for r in revs} >= SKELETON
    for r in revs:
        assert not r.branch_labels, r.revision  # 不用 branch_labels
        assert not r.is_merge_point, r.revision  # 不写 merge 迁移
        if r.revision in SKELETON:
            continue
        assert MODULE_REV.match(r.revision), r.revision
        assert Path(r.path).name in BARE_FILENAMES or Path(r.path).name.startswith(f"{r.revision}_"), (
            r.revision,
            r.path,
        )
        module = r.revision.rsplit("_", 1)[0]
        for owner in REVISION_OWNERS.get(r.revision, (module,)):
            assert (Path("app/modules") / owner).is_dir(), f"{r.revision}：app/modules/{owner} 不存在"


async def test_upgrade_downgrade_base_upgrade_on_temp_db():
    """在临时库上跑（不碰会话共用的 rengu_test，避免和其它测试的连接抢锁）。"""
    skip_forward_only_downgrade("base")
    db = f"rengu_mig_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(
        make_url(BASE_URL).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    await admin.execute(f'CREATE DATABASE "{db}"')
    try:
        cfg = Config("alembic.ini")
        cfg.attributes["database_url"] = (
            make_url(BASE_URL).set(database=db).render_as_string(hide_password=False)
        )
        plain = (
            make_url(BASE_URL).set(drivername="postgresql", database=db).render_as_string(hide_password=False)
        )

        async def custom_types() -> set[tuple[str, str]]:
            conn = await asyncpg.connect(plain)
            try:
                rows = await conn.fetch(CUSTOM_TYPES_SQL)
                return {(r["typname"], r["typtype"]) for r in rows}
            finally:
                await conn.close()

        async def state() -> tuple[int, str | None]:
            conn = await asyncpg.connect(plain)
            try:
                tables = await conn.fetchval(
                    "SELECT count(*) FROM pg_tables"
                    " WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                )
                has_ver = await conn.fetchval("SELECT to_regclass('public.alembic_version') IS NOT NULL")
                ver = await conn.fetchval("SELECT version_num FROM alembic_version") if has_ver else None
                return tables, ver
            finally:
                await conn.close()

        head = _script().get_current_head()
        await asyncio.to_thread(command.upgrade, cfg, "head")
        tables, ver = await state()
        assert tables > 0 and ver == head
        types = await custom_types()
        await asyncio.to_thread(command.downgrade, cfg, "base")
        assert await state() == (0, None)  # 每个 downgrade 都真把表删干净
        assert (
            await custom_types() == set()
        )  # 也不留 enum / 复合类型 / domain（op.drop_table 不会顺带删 enum）
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await state() == (tables, head)
        assert await custom_types() == types

    finally:
        await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", db)
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await admin.close()


async def test_upgrade_head_and_custom_type_discovery():
    """不可降级不影响空库升级、元数据及类型探针的检查。"""
    async with temp_database() as (cfg, dsn):
        await asyncio.to_thread(command.upgrade, cfg, "head")
        state = await db_state(dsn)
        assert state.version == _script().get_current_head() and state.tables
        conn = await asyncpg.connect(dsn)
        try:
            pg14 = int(await conn.fetchval("SHOW server_version_num")) >= 140000
            await conn.execute(
                "CREATE TYPE _probe_enum AS ENUM ('a'); CREATE TYPE _probe_comp AS (x int);"
                " CREATE DOMAIN _probe_dom AS text CHECK (VALUE <> ''); CREATE TABLE _probe_tbl (x int);"
                " CREATE TYPE _probe_range AS RANGE (subtype = int4"
                + (", multirange_type_name = _probe_multirange)" if pg14 else ")")
            )
            assert {
                (r["typname"], r["typtype"]) for r in await conn.fetch(CUSTOM_TYPES_SQL)
            } - state.types == {
                ("_probe_enum", "e"),
                ("_probe_comp", "c"),
                ("_probe_dom", "d"),
                ("_probe_range", "r"),
                *([("_probe_multirange", "m")] if pg14 else []),
            }
        finally:
            await conn.close()


def test_round_trip_skip_preserves_reversible_ranges():
    with pytest.raises(pytest.skip.Exception, match=r"decisions-pending\.md #10"):
        skip_forward_only_downgrade("base")
    skip_forward_only_downgrade("static_0003", start="dynamic_0003")


SKELETON_TABLES = {
    "ontology_release",
    "outbox_event",
    "sys_user",
    "store",
    "product",
    "biz_order",
    "order_line",
}


async def test_skeleton_round_trip_with_helper():
    """骨架自己用 tests/helpers/migrations.py：第一个迁移 0001 的父 = base，降到 base 再升回 head。"""
    from tests.helpers.migrations import assert_module_round_trip, module_parent, revision_module

    assert module_parent("skeleton") == "base"
    assert revision_module("0003") == "skeleton" and revision_module("action_type_0002") == "action_type"
    head = await assert_module_round_trip("skeleton", SKELETON_TABLES)
    from app.db.ddl import GUARD_FUNCTION

    assert GUARD_FUNCTION in head.functions  # 函数也纳入「不留残留」检查


def test_module_parent_follows_down_revision_not_0003(monkeypatch):
    """模块的父迁移按 ScriptDirectory 查：合并后 down_revision 被改指别的 head 也照样对。"""
    from types import SimpleNamespace

    from tests.helpers import migrations as h

    fake = [
        SimpleNamespace(revision=r, down_revision=d)
        for r, d in [
            ("0001", None),
            ("0002", "0001"),
            ("0003", "0002"),
            ("qa_0001", "0003"),
            ("static_0001", "qa_0001"),
            ("static_0002", "static_0001"),
            ("action_0001", "static_0002"),
        ]
    ]
    monkeypatch.setattr(h, "chain", lambda: fake)
    assert h.module_parent("static") == "qa_0001"
    assert h.module_parent("action") == "static_0002"
    assert [s.revision for s in h.module_revisions("static")] == ["static_0001", "static_0002"]
