"""迁移测试辅助（CONVENTIONS §16）：模块迁移往返，不假设自己是 head、也不假设父迁移是 0003。

合并时后到的一方会把第一个迁移的 down_revision 改指当前 head，所以模块测试里**不要写死**
`downgrade "0003"` 或 `get_current_head() == "<模块>_0001"`。用法：

    from tests.helpers.migrations import assert_module_round_trip

    async def test_static_migration_round_trip():
        await assert_module_round_trip("static", STATIC_TABLES, own_types={"some_enum"})

做的事（临时库，测完删掉）：
1. 新库 upgrade 到「模块第一个迁移的 down_revision」（经 ScriptDirectory 查，不写死），
   记下表 / 自定义类型 / 函数；
2. upgrade head：模块自己的表都在、且之前没有；
3. downgrade 回第 1 步那个 revision（链上排在模块后面的迁移也一起降掉）：模块自己的表都消失，
   并且库的状态和第 1 步**完全一致**（不留表、列、索引、触发器、序列、视图、enum / 复合类型 / domain、函数）；
4. 再 upgrade head：和第 2 步完全一致。
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.script import Script, ScriptDirectory
from sqlalchemy.engine import make_url

from tests.conftest import BASE_URL

SKELETON = "skeleton"
SKELETON_REVS = {"0001", "0002", "0003"}
MODULE_REV = re.compile(r"^[a-z_]+_\d{4}$")

# public 下用户定义的类型：enum(e) / 复合(c，只算 CREATE TYPE … AS) / domain(d) / range(r, m)；
# 不算数组类型和扩展自带的类型（如 vector，扩展不随 downgrade 删）。
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
# public 下非扩展的函数（如只追加触发器函数）
FUNCTIONS_SQL = """
SELECT p.proname
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND NOT EXISTS (
    SELECT 1 FROM pg_depend d
    WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
  )
"""

# 列 / 索引 / 触发器 / 序列 / 视图（public，不含 alembic_version）：降回后必须和降之前一致
COLUMNS_SQL = """
SELECT table_name, column_name, data_type, is_nullable, coalesce(column_default, '') AS dflt
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name <> 'alembic_version'
"""
INDEXES_SQL = """
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname = 'public' AND tablename <> 'alembic_version'
"""
TRIGGERS_SQL = """
SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) AS def
FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND NOT t.tgisinternal
"""
SEQUENCES_SQL = "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public'"
VIEWS_SQL = """
SELECT table_name AS name FROM information_schema.views WHERE table_schema = 'public'
UNION ALL
SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'
"""


def script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config("alembic.ini"))


def revision_module(rev: str) -> str:
    """revision id → 模块名：骨架的纯数字 → "skeleton"；`static_0001` → "static"。"""
    if rev in SKELETON_REVS:
        return SKELETON
    assert MODULE_REV.match(rev), rev
    return rev.rsplit("_", 1)[0]


def chain() -> list[Script]:
    """线性链，base → head。"""
    return list(reversed(list(script_directory().walk_revisions())))


def module_revisions(module: str) -> list[Script]:
    """模块自己的迁移（链上顺序）。"""
    return [s for s in chain() if revision_module(s.revision) == module]


def module_parent(module: str) -> str:
    """模块第一个迁移的 down_revision（第一个就是 base 时返回 "base"）。"""
    revs = module_revisions(module)
    assert revs, f"链上没有模块 {module} 的迁移"
    down = revs[0].down_revision
    assert not isinstance(down, tuple), f"{revs[0].revision} 是 merge 迁移"
    return down or "base"


@asynccontextmanager
async def temp_database() -> AsyncIterator[tuple[Config, str]]:
    """临时空库（不碰会话共用的测试库）；yield (alembic Config, asyncpg 用的 dsn)。"""
    db = f"rengu_mig_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(make_url(BASE_URL).set(drivername="postgresql").render_as_string(False))
    await admin.execute(f'CREATE DATABASE "{db}"')
    try:
        cfg = Config("alembic.ini")
        cfg.attributes["database_url"] = make_url(BASE_URL).set(database=db).render_as_string(False)
        yield cfg, make_url(BASE_URL).set(drivername="postgresql", database=db).render_as_string(False)
    finally:
        await admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", db)
        await admin.execute(f'DROP DATABASE IF EXISTS "{db}"')
        await admin.close()


@dataclass(frozen=True)
class DbState:
    tables: frozenset[str]
    types: frozenset[tuple[str, str]]
    functions: frozenset[str]
    version: str | None
    columns: frozenset[tuple[str, ...]] = frozenset()
    indexes: frozenset[tuple[str, str]] = frozenset()
    triggers: frozenset[tuple[str, str, str]] = frozenset()
    sequences: frozenset[str] = frozenset()
    views: frozenset[str] = frozenset()


async def db_state(dsn: str) -> DbState:
    conn = await asyncpg.connect(dsn)
    try:
        tables = {
            r[0]
            for r in await conn.fetch(
                "SELECT tablename FROM pg_tables"
                " WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        }
        has_ver = await conn.fetchval("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        ver = await conn.fetchval("SELECT version_num FROM alembic_version") if has_ver else None
        return DbState(
            frozenset(tables),
            frozenset((r["typname"], r["typtype"]) for r in await conn.fetch(CUSTOM_TYPES_SQL)),
            frozenset(r[0] for r in await conn.fetch(FUNCTIONS_SQL)),
            ver,
            frozenset(tuple(r) for r in await conn.fetch(COLUMNS_SQL)),
            frozenset((r[0], r[1]) for r in await conn.fetch(INDEXES_SQL)),
            frozenset((r[0], r[1], r[2]) for r in await conn.fetch(TRIGGERS_SQL)),
            frozenset(r[0] for r in await conn.fetch(SEQUENCES_SQL)),
            frozenset(r[0] for r in await conn.fetch(VIEWS_SQL)),
        )
    finally:
        await conn.close()


async def assert_module_round_trip(
    module: str, own_tables: Iterable[str], *, own_types: Iterable[str] = ()
) -> DbState:
    """见模块说明。module = revision id 里的模块名（`static`、`action`…；骨架用 "skeleton"）。

    own_tables / own_types（类型名）只列本模块自己建的；别的模块的不用管。返回 head 时的状态。
    """
    own_tables, own_types = set(own_tables), set(own_types)
    assert own_tables, "至少列出一张本模块的表"
    parent = module_parent(module)
    head = script_directory().get_current_head()
    up = asyncio.to_thread
    async with temp_database() as (cfg, dsn):
        if parent != "base":
            await up(command.upgrade, cfg, parent)
        below = await db_state(dsn)
        assert below.version == (None if parent == "base" else parent)
        assert not own_tables & below.tables, f"模块第一个迁移之前就有这些表：{own_tables & below.tables}"

        await up(command.upgrade, cfg, "head")
        at_head = await db_state(dsn)
        assert at_head.version == head
        assert own_tables <= at_head.tables, f"head 上缺表：{own_tables - at_head.tables}"
        head_type_names = {n for n, _ in at_head.types}
        assert own_types <= head_type_names, f"head 上缺类型：{own_types - head_type_names}"

        await up(command.downgrade, cfg, parent)
        after = await db_state(dsn)
        assert not own_tables & after.tables, f"downgrade 后表还在：{own_tables & after.tables}"
        assert after.tables == below.tables, ("downgrade 后表不一致", after.tables ^ below.tables)
        assert after.types == below.types, ("downgrade 留下 / 删掉了类型", after.types ^ below.types)
        assert after.functions == below.functions, ("函数不一致", after.functions ^ below.functions)
        for f in ("columns", "indexes", "triggers", "sequences", "views"):
            a, b = getattr(after, f), getattr(below, f)
            assert a == b, (f"downgrade 后{f}不一致", a ^ b)
        assert after.version == below.version

        await up(command.upgrade, cfg, "head")
        assert await db_state(dsn) == at_head
        return at_head
