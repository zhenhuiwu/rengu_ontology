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
        # PDE must-fix 1（二审）：带 schema 的函数调用（白名单函数也不行，防 search_path / 同名函数）
        "select pg_catalog.lower(name) from store",
        "select public.age(shipped_at) from biz_order",
        "select x.age(shipped_at) from biz_order",
        "select * from store where name = pg_catalog.upper('a')",
        # PDE must-fix 2（二审）：类型转换白名单——:: / CAST / TRY_CAST 三种写法逐个
        "select 'pg_authid'::regclass",
        "select cast('pg_authid' as regclass)",
        "select try_cast('pg_authid' as regclass)",
        "select 1::oid",
        "select cast(1 as oid)",
        "select 'now'::regproc",
        "select cast('int4' as regtype)",
        "select try_cast('int4' as regtype)",
        "select name::text::regclass from store",
        "select cast(name as myschema.t) from store",
        "select name::myschema.t from store",
        "select name::pg_catalog.text from store",
        "select try_cast(name as public.t) from store",
        "select name::t from store",  # 自定义类型
        "select name::json from store",
        "select name::bytea from store",
        "select array[1]::int[]",
        # TABLESAMPLE
        "select * from store tablesample system (10)",
        "select * from biz_order tablesample bernoulli (1) repeatable (42)",
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
        # 类型转换白名单：数值 / 文本 / 日期时间 / 布尔
        "select amount::numeric(12, 2), amount::int, amount::bigint, amount::smallint,"
        " amount::float, amount::double precision, amount::decimal from biz_order",
        "select channel::text, channel::varchar(8), channel::char(2) from biz_order",
        "select created_at_biz::date, created_at_biz::timestamp, created_at_biz::timestamptz,"
        " cast(created_at_biz as time), try_cast(channel as int) from biz_order",
        "select is_test::boolean, cast(1 as bool) from biz_order"
        " where created_at_biz >= date '2026-10-01' - interval '1 day'",
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
    assert "JOIN public.biz_order AS o" in sql and params == ["华东"]  # 不过滤的表也固定 public
    assert "'华东'" not in sql  # 值走绑定参数，不拼进 SQL


def test_inject_renumbers_params_across_tables():
    sql, params = inject_row_filters(
        "select * from store s join biz_order o on o.store_id = s.id",
        {"store": REGION, "biz_order": ("channel = $1 AND status = $2", ["美团闪购", "shipped"])},
    )
    assert "big_region = $1" in sql and "channel = $2 AND status = $3" in sql
    assert params == ["华东", "美团闪购", "shipped"]


def test_inject_pins_unfiltered_and_allowed_tables_to_public():
    """不过滤（filter None）和 allowed 白名单表都改写成 public.<表>，不依赖 search_path。"""
    sql, params = inject_row_filters(
        "select * from product p join biz_order o on true join store on true",
        {"biz_order": None, "store": None},
        frozenset({"product"}),
    )
    assert "FROM public.product AS p" in sql and "JOIN public.biz_order AS o" in sql
    assert "JOIN public.store" in sql and params == []
    # CTE 引用不是真表，不改写
    sql, _ = inject_row_filters(
        "with product as (select 1 as id) select * from product", {}, frozenset({"product"})
    )
    assert sql.endswith("FROM product")


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
    assert {"store", "biz_order", "order_line"} <= g.keys() and "product" not in g
    assert g["order_line"].scope_object_type == "order"
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


# ---- order_line 经 order_id 继承 biz_order 的行级权限；product 维度表显式放行 ----
ORDER_SCOPE = {"field": "order_belongs_to_store.big_region", "op": "=", "valueFrom": "user.region"}


def test_product_registered_unrestricted_explicitly():
    from app.common.sql_guard import (
        GovernedTable,
        register_governed,
        register_unrestricted,
        unrestricted_tables,
    )

    g = _gov()
    assert "product" in unrestricted_tables() and "product" not in g
    with pytest.raises(ValueError):
        register_unrestricted("order_line")  # 受控表不能再登记为放行
    with pytest.raises(ValueError):
        register_governed(GovernedTable(g["store"].table.metadata.tables["product"], "product", {}))
    # 没登记的表照样拒绝（不是默认放行）
    with pytest.raises(SqlRejected):
        inject_row_filters("select * from sys_user", {}, unrestricted_tables())


def test_compile_order_line_inherits_order_predicate():
    g = _gov()["order_line"]
    sql, params = compile_predicate(g, ORDER_SCOPE, {"region": "华东"})
    assert sql.startswith(
        "EXISTS(SELECT 1 FROM biz_order AS _rf_biz_order WHERE _rf_biz_order.id = order_line.order_id"
    )
    assert "EXISTS(SELECT 1 FROM store WHERE store.id = _rf_biz_order.store_id" in sql
    assert params == ["华东"] and "'华东'" not in sql
    assert compile_predicate(g, None, {}) is None
    assert compile_predicate(g, {"deny": True}, {}) == DENY


def test_inject_order_line_pins_public_and_renumbers():
    g = _gov()
    filters = {
        "store": compile_predicate(g["store"], {"field": "big_region", "op": "=", "value": "华南"}, {}),
        "order_line": compile_predicate(g["order_line"], ORDER_SCOPE, {"region": "华东"}),
    }
    sql, params = inject_row_filters(
        "with biz_order as (select 1 as id), store as (select 1 as id) "
        "select s.name, sum(l.qty * l.price) from store s, order_line l "
        "join biz_order o on o.id = l.order_id "
        "join store s2 on true group by 1",
        {**filters, "biz_order": None},
    )
    assert "FROM public.biz_order AS _rf_biz_order" in sql and "FROM public.store WHERE store.id" in sql
    assert ") AS l JOIN biz_order AS o" in sql  # 用户的 biz_order 是 CTE，不替换；谓词里的固定 public
    assert params == ["华东"]  # 其它 store 引用都是 CTE，不注入
    sql2, params2 = inject_row_filters(
        "select * from store s join order_line l on true", {**filters, "biz_order": None}
    )
    assert "big_region = CAST($1 AS VARCHAR)" in sql2 and "big_region = CAST($2 AS VARCHAR)" in sql2
    assert params2 == ["华南", "华东"]


def test_order_line_non_public_schema_still_rejected():
    for q in (
        "select * from other.order_line",
        "select * from pg_catalog.pg_class c join order_line l on true",
        "with x as (select * from information_schema.columns) select * from order_line, x",
    ):
        with pytest.raises(SqlRejected):
            inject_row_filters(q, {"order_line": DENY}, frozenset({"product"}))


async def test_e2e_order_line_inherits_region(biz, conn, monkeypatch):
    import app.common.auth as auth

    _gov()
    await biz.execute(text("UPDATE store SET big_region = '华南' WHERE code IN ('SH0031', 'SH0032')"))
    gmv_sql = (
        "SELECT round(sum(l.qty * l.price), 2) FROM order_line l JOIN biz_order o ON o.id = l.order_id "
        "JOIN store s ON s.id = o.store_id WHERE s.big_region = :r"
    )
    east = await biz.scalar(text(gmv_sql), {"r": "华东"})
    total = await biz.scalar(text("SELECT round(sum(qty * price), 2) FROM order_line"))
    assert 0 < east < total

    async def scope(_user, obj):
        field = "big_region" if obj == "store" else "order_belongs_to_store.big_region"
        return {"field": field, "op": "=", "valueFrom": "user.region"}

    monkeypatch.setattr(auth, "row_filter", scope)
    user = _U(attrs={"region": "华东"})

    async def q(sql, u=user):
        return (await run_guarded(conn, sql, u)).scalar()

    # 不连 biz_order：只查订单行
    assert await q("select round(sum(qty * price), 2) from order_line") == east
    # 连 biz_order / store / product
    assert (
        await q(
            "select round(sum(l.qty * l.price), 2) from order_line l join biz_order o on o.id = l.order_id"
            " join store s on s.id = o.store_id join product p on p.id = l.product_id where not o.is_test"
        )
        == east
    )
    # 想用 or 1=1 / 子查询绕开：注入在表引用上，用户 where 绕不开
    assert await q("select round(sum(qty * price), 2) from order_line where 1=1 or true") == east
    assert (
        await q("select round(sum(qty*price), 2) from (select * from order_line) x where x.order_id > 0")
        == east
    )
    # 同名 CTE 劫持：CTE 叫 order_line / biz_order / store 都不影响谓词
    hijacks = [
        "with biz_order as (select id, id as store_id from public.biz_order) "
        "select round(sum(qty * price), 2) from order_line",
        "with store as (select id, '华东'::text as big_region from public.biz_order) "
        "select round(sum(qty * price), 2) from order_line",
        "with order_line as (select * from order_line) select round(sum(qty * price), 2) from order_line",
        "with _rf_biz_order as (select 1 as id) select round(sum(_rf_biz_order.qty * _rf_biz_order.price), 2)"
        " from order_line as _rf_biz_order",
    ]
    for h in hijacks:
        assert await q(h) == east, h
    # 用户属性里塞 SQL 只是参数值
    assert await q("select count(*) from order_line", _U(attrs={"region": "华东' OR '1'='1"})) == 0
    # 没有属性 / 桩实现的非管理员 → 看不到
    assert await q("select count(*) from order_line", _U()) == 0
    monkeypatch.undo()
    assert await q("select count(*) from order_line", _U()) == 0
    # product 维度表不过滤（非管理员桩也能查全量）
    assert await q("select count(*) from product", _U()) == 12
    # 管理员看全量
    assert await q("select round(sum(qty * price), 2) from order_line", _U(admin=True)) == total
