"""问数网关：只读单条 SELECT、函数白名单、schema、CTE 作用域、绑定参数谓词、一跳 EXISTS 端到端、只读引擎。"""

import pytest
from sqlalchemy import text

from app.common.sql_guard import (
    DENY,
    SqlRejected,
    assert_single_select,
    compile_predicate,
    governed_tables,
    inject_row_filters,
    row_filters_for,
    run_guarded,
)

REGION = ("big_region = $1", ["华东"])


@pytest.mark.parametrize(
    "sql",
    [
        "select 1; select 2",
        "delete from store",
        "with x as (delete from store returning *) select * from x",
        "select * into t2 from store",
        "select * from store for update",
        "select pg_sleep(10)",
        "select dblink('x','y')",
        "drop table store",
        # PDE must-fix 2：函数白名单（以下都解析成 Anonymous 或不在白名单）
        "select query_to_xml('select * from sys_user', true, true, '')",
        "select pg_stat_file('/etc/passwd')",
        "select current_setting('is_superuser')",
        "select pg_advisory_lock(1)",
        "select set_config('default_transaction_read_only', 'off', false)",
        "select lo_import('/etc/passwd')",
        "select * from store where name = pg_read_file('x')",
        # PDE must-fix 1：public 以外的 schema
        "select * from pg_catalog.pg_user",
        "select * from information_schema.tables",
        "select * from other.store",
        "select * from rengu.public.store",
        # 绑定参数占位符不允许出现在用户 SQL 里（参数只来自谓词）
        "select * from store where id = $1",
        "select * from store where id = :id",
    ],
)
def test_rejects(sql):
    with pytest.raises(SqlRejected):
        assert_single_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "select count(*), sum(amount), avg(amount), max(created_at_biz) from biz_order",
        "select date_trunc('day', created_at_biz), coalesce(warehouse, '-') from biz_order group by 1, 2",
        "select row_number() over (order by amount desc), round(amount, 1), upper(channel) from biz_order",
        "select case when amount > 100 then 'a' else 'b' end, cast(amount as int),"
        " extract(hour from shipped_at)"
        " from biz_order where channel like '美团%' and amount between 1 and 2",
        "select age(shipped_at, created_at_biz) from public.biz_order",
    ],
)
def test_allows_whitelisted(sql):
    assert_single_select(sql)


def test_inject_params_and_alias():
    sql, params = inject_row_filters(
        "select s.name, sum(o.amount) from store s join biz_order o on o.store_id = s.id group by s.name",
        {"store": REGION, "biz_order": None},
    )
    assert "(SELECT * FROM public.store WHERE big_region = $1) AS s" in sql
    assert "JOIN biz_order AS o" in sql and params == ["华东"]
    assert "'华东'" not in sql  # 值走绑定参数，不拼进 SQL


def test_inject_renumbers_params_across_tables():
    sql, params = inject_row_filters(
        "select * from store s join biz_order o on o.store_id = s.id",
        {"store": REGION, "biz_order": ("channel = $1 AND status = $2", ["美团闪购", "shipped"])},
    )
    assert "big_region = $1" in sql and "channel = $2 AND status = $3" in sql
    assert params == ["华东", "美团闪购", "shipped"]


def test_inject_deny_all_and_unknown_table():
    assert "WHERE FALSE" in inject_row_filters("select * from store", {"store": DENY})[0]
    with pytest.raises(SqlRejected):
        inject_row_filters("select * from sys_user", {"store": None})
    assert inject_row_filters("select * from product", {}, frozenset({"product"}))[1] == []


# ---- PDE must-fix 1：CTE 绕过 ----
def test_cte_same_name_still_filtered():
    """with store as (select * from store) — 内层是真表，必须加过滤；外层引用的是 CTE。"""
    sql, params = inject_row_filters(
        "with store as (select * from store) select * from store", {"store": REGION}
    )
    assert sql.count("public.store WHERE big_region = $1") == 1 and params == ["华东"]
    assert "WITH store AS (SELECT * FROM (SELECT * FROM public.store" in sql


def test_cte_alias_and_nested_subquery_filtered():
    for q in (
        "with x as (select * from store) select * from x",
        "select * from (select * from (select * from store) a) b",
        "select * from biz_order where store_id in (select id from store)",
        "select * from biz_order o where exists (select 1 from store where store.id = o.store_id)",
    ):
        sql, _ = inject_row_filters(q, {"store": REGION, "biz_order": None})
        assert "public.store WHERE big_region = $1" in sql, q


def test_cte_cannot_shadow_table_inside_predicate():
    """谓词里的 EXISTS 子查询表固定到 public，用户同名 CTE 劫持不了。"""
    pred = (
        "EXISTS(SELECT 1 FROM store WHERE store.id = biz_order.store_id AND store.big_region = $1)",
        ["华东"],
    )
    sql, _ = inject_row_filters(
        "with store as (select 1 as id, '华东' as big_region) select * from biz_order",
        {"biz_order": pred, "store": None},
    )
    assert "FROM public.store WHERE store.id = biz_order.store_id" in sql


def test_unknown_schema_in_cte_rejected():
    with pytest.raises(SqlRejected):
        inject_row_filters("with x as (select * from pg_catalog.pg_authid) select * from x", {})


# ---- 登记 + 编译谓词（biz 的 store / biz_order）----
def _gov():
    import app.modules.biz.governance  # noqa: F401  登记 store / biz_order

    return governed_tables()


def test_biz_governed_tables_registered():
    g = _gov()
    assert {"store", "biz_order"} <= g.keys() and "order_line" not in g
    assert g["store"].field_of_prop["大区"] == "big_region"
    assert g["biz_order"].field_of_prop["大区"] == "order_belongs_to_store.big_region"


def test_compile_one_hop_predicate():
    g = _gov()["biz_order"]
    sql, params = compile_predicate(
        g, {"field": "order_belongs_to_store.big_region", "op": "in", "value": ["华东", "华南"]}, {}
    )
    assert sql.startswith("EXISTS(SELECT 1 FROM store WHERE store.id = biz_order.store_id")
    assert "$1" in sql and "$2" in sql and params == ["华东", "华南"]
    assert compile_predicate(g, None, {}) is None
    assert compile_predicate(g, {"deny": True}, {}) == DENY


# ---- PDE must-fix 3：端到端（真库执行，带一跳 EXISTS 和绑定参数）----
@pytest.fixture
async def biz(session):
    from app.seeds.loader import run_seeds

    await run_seeds(session, ["admin", "biz"])
    await session.flush()
    return session


class _U:
    def __init__(self, admin=False, attrs=None):
        self.id, self.name, self.is_ontology_admin, self.attrs = "U-T", "t", admin, attrs or {}


async def test_e2e_one_hop_exists_filters_orders(biz, conn, monkeypatch):
    import app.common.auth as auth

    _gov()
    await biz.execute(text("UPDATE store SET big_region = '华南' WHERE code = 'SH0031'"))
    expected = await biz.scalar(
        text("SELECT count(*) FROM biz_order o JOIN store s ON s.id = o.store_id WHERE s.big_region = '华东'")
    )
    total = await biz.scalar(text("SELECT count(*) FROM biz_order"))
    assert 0 < expected < total

    async def scope(_user, obj):
        field = "big_region" if obj == "store" else "order_belongs_to_store.big_region"
        return {"field": field, "op": "=", "valueFrom": "user.region"}

    monkeypatch.setattr(auth, "row_filter", scope)
    user = _U(attrs={"region": "华东"})
    q = "select count(*) from biz_order"
    res = await run_guarded(conn, q, user)
    assert res.scalar() == expected
    # 同名 CTE 劫持不了谓词里的 store
    q2 = "with store as (select id, '华东'::text as big_region from biz_order) select count(*) from biz_order"
    assert (await run_guarded(conn, q2, user)).scalar() == expected
    # 用户属性里塞 SQL 也只是参数值
    evil = _U(attrs={"region": "华东' OR '1'='1"})
    assert (await run_guarded(conn, q, evil)).scalar() == 0
    # 没有该属性 → 什么都看不到
    assert (await run_guarded(conn, q, _U())).scalar() == 0
    filters = await row_filters_for(user)
    assert filters["store"] == ("store.big_region = CAST($1 AS VARCHAR)", ["华东"])


async def test_e2e_stub_denies_non_admin(biz, conn):
    _gov()
    assert (await run_guarded(conn, "select count(*) from store", _U(admin=False))).scalar() == 0
    assert (await run_guarded(conn, "select count(*) from store", _U(admin=True))).scalar() == 95


# ---- PDE must-fix 4：只读引擎 ----
async def test_qa_readonly_engine_requires_config(monkeypatch):
    from app.core.config import get_settings
    from app.db import session as dbs

    monkeypatch.setattr(get_settings(), "qa_readonly_database_url", None)
    monkeypatch.setattr(dbs, "_qa_engine", None)
    with pytest.raises(dbs.QaReadonlyNotConfigured):
        dbs.get_qa_readonly_engine()


async def test_qa_readonly_engine_settings(monkeypatch):
    from sqlalchemy.exc import DBAPIError

    from app.core.config import get_settings
    from app.db import session as dbs

    s = get_settings()
    monkeypatch.setattr(s, "qa_readonly_database_url", s.database_url)
    monkeypatch.setattr(s, "qa_statement_timeout_ms", 1234)
    monkeypatch.setattr(dbs, "_qa_engine", None)
    eng = dbs.get_qa_readonly_engine()
    try:
        assert dbs.get_qa_readonly_engine() is eng
        async with eng.connect() as c:
            assert await c.scalar(text("SHOW statement_timeout")) == "1234ms"
            assert await c.scalar(text("SHOW default_transaction_read_only")) == "on"
            with pytest.raises(DBAPIError, match="read-only"):
                await c.execute(text("CREATE TEMP TABLE qa_w (x int)"))
            await c.rollback()
            with pytest.raises(DBAPIError, match="statement timeout"):
                await c.execute(text("SELECT pg_sleep(3)"))
    finally:
        await eng.dispose()
        monkeypatch.setattr(dbs, "_qa_engine", None)
