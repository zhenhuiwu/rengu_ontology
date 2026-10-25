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
        "select '1'::interval[]",
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
        # PDE 二审不阻塞：带字段限定的 interval
        "select '1'::interval day, cast('1' as interval hour), '1'::interval day to second",
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
    assert {"store", "biz_order", "order_line", "product"} <= g.keys()
    assert g["product"].field_of_prop == {"品类": "category_l1"}
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
    # 数据只在本事务里、表从没 analyze 过 → 规划器按 1 行估，多层 EXISTS 会退化成嵌套循环（跑几分钟）
    for t in ("store", "product", "biz_order", "order_line"):
        await session.execute(text(f"ANALYZE {t}"))
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
        if obj == "product":  # 商品没配范围的角色这里按「不可见」给（只验证订单 / 门店）
            return {"deny": True}
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


def test_unrestricted_registry_and_unregistered_tables_rejected():
    from app.common.sql_guard import register_unrestricted, unrestricted_tables

    _gov()
    assert (
        "product" not in unrestricted_tables()
    )  # 品类范围落地后 product 改为受控（只对有品类范围的用户过滤）
    with pytest.raises(ValueError):
        register_unrestricted("order_line")  # 受控表不能再登记为放行
    with pytest.raises(ValueError):
        register_unrestricted("product")
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
        if obj == "product":  # 能看商品、没配品类范围 → 不限制
            return None
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
    # product：桩实现对非管理员一律 DENY_ALL（不再有「没配品类就放开」的特例）
    assert await q("select count(*) from product", _U()) == 0
    # 管理员看全量
    assert await q("select round(sum(qty * price), 2) from order_line", _U(admin=True)) == total


# ---- static-api.md §1.5（静态 9c7fe84）：订单.门店、商品.品类 ----
def _scoped(monkeypatch, by_obj: dict[str, list]):
    """row_filter 桩：按对象类型给角色，经 effective_expr 合并。

    值是 RowScope 列表 → 一个有查看权、非 defaultDeny 的角色（空列表 = 该角色不限范围）；
    值是 RoleAccess 列表 → 照给的角色。没给的对象 → 没有角色能看 → DENY_ALL。
    """
    import app.common.auth as auth
    from app.common.sql_guard import RoleAccess, effective_expr

    _gov()

    def roles(v: list) -> list[RoleAccess]:
        if v and isinstance(v[0], RoleAccess):
            return v
        return [RoleAccess(view=True, scopes=tuple(v))]

    async def fake(user, obj):
        if user.is_ontology_admin:
            return None
        return effective_expr(obj, roles(by_obj.get(obj, [])) if obj in by_obj else [])

    monkeypatch.setattr(auth, "row_filter", fake)


def _sc(prop, *values):
    return {"prop": prop, "op": "∈" if len(values) > 1 else "=", "values": list(values)}


def test_category_alias_contract():
    from app.contracts.static import CATEGORY_SCOPE_ALIAS

    # roles.ts SCOPE_PROPS 商品.品类 的 6 个取值都有映射；映射目标是 seed 里的一级类目
    assert set(CATEGORY_SCOPE_ALIAS) == {"生鲜", "乳品烘焙", "粮油调味", "休闲零食", "酒水饮料", "日化清洁"}
    assert CATEGORY_SCOPE_ALIAS["乳品烘焙"] == "乳品" and CATEGORY_SCOPE_ALIAS["日化清洁"] == "日化家清"
    assert CATEGORY_SCOPE_ALIAS["休闲零食"] == "休闲食品" and CATEGORY_SCOPE_ALIAS["生鲜"] == "生鲜"
    with pytest.raises(TypeError):
        CATEGORY_SCOPE_ALIAS["x"] = "y"  # type: ignore[index]


def test_scope_expr_maps_props_values_and_ors():
    from app.common.sql_guard import scope_expr

    g = _gov()
    assert scope_expr(g["product"], [_sc("品类", "乳品烘焙", "冷冻")]) == {
        "field": "category_l1",
        "op": "in",
        "value": ["乳品", "冷冻"],  # 别名换名；不在表里的值原样保留
    }
    both = scope_expr(g["biz_order"], [_sc("大区", "华东"), _sc("门店", "上海徐汇店")])
    assert both == {  # 多条范围 OR [推断]（static 0cfb2eb §1.5）
        "or": [
            {"field": "order_belongs_to_store.big_region", "op": "=", "value": "华东"},
            {"field": "order_belongs_to_store.name", "op": "=", "value": "上海徐汇店"},
        ]
    }
    assert scope_expr(g["biz_order"], []) is None


def test_compile_category_and_store_name_predicates():
    from app.common.sql_guard import scope_expr

    g = _gov()
    sql, params = compile_predicate(
        g["product"], scope_expr(g["product"], [_sc("品类", "生鲜", "日化清洁")]), {}
    )
    assert sql.startswith("SPLIT_PART(product.category, '/', 1) IN (")
    assert params == ["生鲜", "日化家清"] and "'生鲜'" not in sql
    # 不限制（None）/ 不可见（DENY_ALL）和别的受控表一样；「没配品类范围」由 effective_expr 给 None
    assert compile_predicate(g["product"], {"deny": True}, {}) == DENY
    assert compile_predicate(g["product"], None, {}) is None
    sql, params = compile_predicate(
        g["biz_order"], scope_expr(g["biz_order"], [_sc("门店", "A店", "B店")]), {}
    )
    assert sql.startswith(
        "EXISTS(SELECT 1 FROM store WHERE store.id = biz_order.store_id AND store.name IN ("
    )
    assert params == ["A店", "B店"]
    # 订单没配范围照旧拒绝（只有 product 是「没配 = 不限制」）
    assert compile_predicate(g["biz_order"], {"deny": True}, {}) == DENY


def test_inject_product_pins_public_and_renumbers():
    from app.common.sql_guard import scope_expr

    g = _gov()
    filters = {
        "product": compile_predicate(g["product"], scope_expr(g["product"], [_sc("品类", "乳品烘焙")]), {}),
        "order_line": compile_predicate(
            g["order_line"], scope_expr(g["biz_order"], [_sc("门店", "X店")]), {}
        ),
    }
    sql, params = inject_row_filters(
        "with product as (select 1 as id) "
        "select * from order_line l join public.product p on p.id = l.product_id",
        filters,
    )
    assert (
        "(SELECT * FROM public.product WHERE SPLIT_PART(product.category, '/', 1) = CAST($2 AS VARCHAR)) AS p"
        in sql
    )
    assert "store.name = CAST($1 AS VARCHAR)" in sql and params == ["X店", "乳品"]


async def test_e2e_product_category_scope(biz, conn, monkeypatch):
    async def q(sql, u=None):
        return (await run_guarded(conn, sql, u or _U())).scalar()

    async def truth(*l1):
        return await biz.scalar(
            text("SELECT count(*) FROM product WHERE split_part(category, '/', 1) = ANY(:v)"), {"v": list(l1)}
        )

    dairy, fresh = await truth("乳品"), await truth("生鲜")
    assert dairy > 0 and fresh > 0 and dairy + fresh < 12
    # 能看商品、但角色没配品类范围 → product 不过滤；对商品没有查看权 → 拒绝
    _scoped(monkeypatch, {"order": [_sc("大区", "华东")], "product": []})
    assert await q("select count(*) from product") == 12
    _scoped(monkeypatch, {"order": [_sc("大区", "华东")]})
    assert await q("select count(*) from product") == 0
    # 别名换名：乳品烘焙 → 乳品；生鲜同名
    _scoped(monkeypatch, {"product": [_sc("品类", "乳品烘焙")]})
    assert await q("select count(*) from product") == dairy
    assert await q("select count(*) from product p where p.category like '生鲜%' or true") == dairy
    _scoped(monkeypatch, {"product": [_sc("品类", "乳品烘焙", "生鲜")]})
    assert await q("select count(*) from product") == dairy + fresh
    # 同名 CTE 劫持不了；子查询绕不开
    assert await q("with product as (select * from product) select count(*) from product") == dairy + fresh
    assert await q("select count(*) from (select * from public.product) x") == dairy + fresh
    # 不在别名表、库里也没有的一级类目 → 0 行；和已知值混着给只匹配已知的
    _scoped(monkeypatch, {"product": [_sc("品类", "冷冻食品")]})
    assert await q("select count(*) from product") == 0
    _scoped(monkeypatch, {"product": [_sc("品类", "冷冻食品", "休闲零食")]})
    assert await q("select count(*) from product") == await truth("休闲食品") == 1
    # 塞 SQL 只是参数值
    _scoped(monkeypatch, {"product": [_sc("品类", "生鲜' OR '1'='1")]})
    assert await q("select count(*) from product") == 0
    # order_line 只继承订单范围、不按品类过滤（已定：静态，roles.ts:62）
    _scoped(monkeypatch, {"product": [_sc("品类", "乳品烘焙")], "order": [_sc("大区", "华东")]})
    east_lines = await biz.scalar(
        text(
            "SELECT count(*) FROM order_line l JOIN biz_order o ON o.id = l.order_id "
            "JOIN store s ON s.id = o.store_id WHERE s.big_region = '华东'"
        )
    )
    assert await q("select count(*) from order_line") == east_lines
    # 连 product 时，product 这一侧照样按品类过滤（inner join 后只剩乳品的行）
    dairy_east = await biz.scalar(
        text(
            "SELECT count(*) FROM order_line l JOIN biz_order o ON o.id = l.order_id "
            "JOIN store s ON s.id = o.store_id JOIN product p ON p.id = l.product_id "
            "WHERE s.big_region = '华东' AND split_part(p.category, '/', 1) = '乳品'"
        )
    )
    assert await q("select count(*) from order_line l join product p on p.id = l.product_id") == dairy_east
    # 管理员不限制
    assert await q("select count(*) from product", _U(admin=True)) == 12


async def test_e2e_order_store_name_scope(biz, conn, monkeypatch):
    async def q(sql, u=None):
        return (await run_guarded(conn, sql, u or _U())).scalar()

    a, b = "无锡梁溪崇安寺店", "合肥政务区天鹅湖店"
    await biz.execute(text("UPDATE store SET big_region = '华南' WHERE name = :n"), {"n": b})
    by_name = text(
        "SELECT count(*) FROM biz_order o JOIN store s ON s.id = o.store_id WHERE s.name = ANY(:n)"
        " AND (CAST(:r AS text) IS NULL OR s.big_region = :r)"
    )
    n_a = await biz.scalar(by_name, {"n": [a], "r": None})
    n_ab = await biz.scalar(by_name, {"n": [a, b], "r": None})
    assert 0 < n_a < n_ab
    # 门店名原样比
    _scoped(monkeypatch, {"order": [_sc("门店", a)]})
    assert await q("select count(*) from biz_order") == n_a
    _scoped(monkeypatch, {"order": [_sc("门店", a, b)]})
    assert await q("select count(*) from biz_order") == n_ab
    # 同名 CTE 劫持不了谓词里的 store
    hijack = (
        f"with store as (select id, '{a}'::text as name from public.store) select count(*) from biz_order"
    )
    assert await q(hijack) == n_ab
    # mock roles.ts:26 的简称在 seed 里一个都对不上 → 0 行（用户已定 2026-10-25：不加别名表）
    _scoped(monkeypatch, {"order": [_sc("门店", "上海徐汇店")]})
    assert await q("select count(*) from biz_order") == 0
    # 同一角色上和大区同时配 → 也是 OR [推断]（static 0cfb2eb §1.5 / roles.ts:114）：b 已改成华南
    region_or_name = text(
        "SELECT count(*) FROM biz_order o JOIN store s ON s.id = o.store_id"
        " WHERE s.big_region = :r OR s.name = ANY(:n)"
    )
    for r in ("华东", "华南"):
        _scoped(monkeypatch, {"order": [_sc("大区", r), _sc("门店", a, b)]})
        want = await biz.scalar(region_or_name, {"r": r, "n": [a, b]})
        only_region = await biz.scalar(region_or_name, {"r": r, "n": []})
        assert want > only_region and await q("select count(*) from biz_order") == want
    # 分在两个角色上 → OR（roles.ts:112-117 effectiveAccess）：华东大区的全部订单 ∪ 门店 b 的订单
    from app.common.sql_guard import RoleAccess

    east = await biz.scalar(
        text("SELECT count(*) FROM biz_order o JOIN store s ON s.id = o.store_id WHERE s.big_region = '华东'")
    )
    n_b = n_ab - n_a
    two_roles = [RoleAccess(True, scopes=(_sc("大区", "华东"),)), RoleAccess(True, scopes=(_sc("门店", b),))]
    _scoped(monkeypatch, {"order": two_roles})
    assert await q("select count(*) from biz_order") == east + n_b  # b 在华南，不重叠
    # order_line 继承门店名范围（两跳）
    lines = text(
        "SELECT count(*) FROM order_line l JOIN biz_order o ON o.id = l.order_id "
        "JOIN store s ON s.id = o.store_id WHERE s.name = :n"
    )
    _scoped(monkeypatch, {"order": [_sc("门店", a)]})
    n_lines = await biz.scalar(lines, {"n": a})
    assert n_lines > n_a  # 一单多行
    assert await q("select count(*) from order_line") == n_lines
    assert (
        await q("with biz_order as (select * from public.biz_order) select count(*) from order_line")
        == n_lines
    )
    # 参数编号：store 一条范围、订单两条范围（OR）、订单行继承，一条 SQL 里合并
    _scoped(monkeypatch, {"order": [_sc("大区", "华南"), _sc("门店", a)], "store": [_sc("大区", "华东")]})
    joined = (
        "select count(*) from order_line l join biz_order o on o.id = l.order_id"
        " join store s on s.id = o.store_id"
    )
    # 订单：华南（只有 b）∪ 门店 a；store 只剩华东 → b 被 join 掉，只剩 a 的订单行
    assert await q(joined) == n_lines


# ---- 未映射的范围一律拒绝（fail closed，static 4641ebf §1.5：一期没有 member 表，会员范围按拒绝）----
def test_unmapped_scope_fails_closed():
    from app.common.row_filter import DENY_ALL, combine_or
    from app.common.sql_guard import RoleAccess, effective_expr, scope_expr, scope_expr_for_object

    g = _gov()
    # 对象上没映射的属性（订单.品类、商品.品牌）：这条 → DENY_ALL，不是被忽略；OR 里它不贡献行
    east = {"field": "order_belongs_to_store.big_region", "op": "=", "value": "华东"}
    assert scope_expr(g["biz_order"], [_sc("品类", "生鲜")]) == DENY_ALL
    assert scope_expr(g["biz_order"], [_sc("大区", "华东"), _sc("品类", "生鲜")]) == east
    assert scope_expr(g["product"], [_sc("品牌", "光明")]) == DENY_ALL
    # 一期没建表的对象（会员、库存快照）配了范围 → DENY_ALL；没配 → None
    assert scope_expr_for_object("member", [_sc("常去门店所属大区", "华东")]) == DENY_ALL
    assert scope_expr_for_object("inventory_snapshot", [_sc("仓库", "上海嘉定仓")]) == DENY_ALL
    assert scope_expr_for_object("member", []) is None
    assert effective_expr("member", [RoleAccess(True, scopes=(_sc("常去门店所属大区", "华东"),))]) == DENY_ALL
    for t in ("product", "biz_order", "order_line"):
        assert compile_predicate(g[t], DENY_ALL, {}) == DENY
    # 多角色：映射不了的角色不贡献范围（不会把别的角色放宽，也不会被当成「不限制」）
    ok = {"field": "big_region", "op": "=", "value": "华东"}
    assert combine_or([DENY_ALL, ok]) == ok
    assert combine_or([DENY_ALL]) == DENY_ALL and combine_or([]) == DENY_ALL
    assert combine_or([DENY_ALL, None]) is None


def test_effective_expr_follows_roles_ts():
    """roles.ts:110-117 effectiveAccess（static 0db86b3 §1.5）。"""
    from app.common.row_filter import DENY_ALL
    from app.common.sql_guard import RoleAccess, effective_expr

    _gov()
    east = _sc("大区", "华东")
    east_expr = {"field": "order_belongs_to_store.big_region", "op": "=", "value": "华东"}
    store_expr = {"field": "order_belongs_to_store.name", "op": "=", "value": "X店"}
    # 没有角色 / 没有查看权 → 拒绝
    assert effective_expr("order", []) == DENY_ALL
    assert effective_expr("order", [RoleAccess(False)]) == DENY_ALL
    assert effective_expr("order", [RoleAccess(False), RoleAccess(True, scopes=(east,))]) == east_expr
    # defaultDeny 且该对象没配范围（R-06 对会员）：单独持有 → 拒绝，不是不限制
    r06_member = RoleAccess(True, default_deny=True)
    assert effective_expr("member", [r06_member]) == DENY_ALL
    assert effective_expr("order", [r06_member]) == DENY_ALL
    # 混合：defaultDeny 没配范围的角色不算「不限制」，只剩有范围那个角色的谓词
    assert effective_expr("order", [r06_member, RoleAccess(True, scopes=(east,))]) == east_expr
    # 普通角色有查看权且没配范围 → 整体不限制
    assert effective_expr("order", [RoleAccess(True), RoleAccess(True, scopes=(east,))]) is None
    # defaultDeny 但配了该对象范围 → 按范围
    assert effective_expr("order", [RoleAccess(True, default_deny=True, scopes=(east,))]) == east_expr
    # 跨角色 OR；同一角色内多条也 OR [推断]（static 0cfb2eb）
    st = _sc("门店", "X店")
    two = [RoleAccess(True, scopes=(east,)), RoleAccess(True, scopes=(st,))]
    assert effective_expr("order", two) == {"or": [east_expr, store_expr]}
    one = [RoleAccess(True, scopes=(east, st))]
    assert effective_expr("order", one) == {"or": [east_expr, store_expr]}
    # 某角色映射不了 → 它不贡献；另一角色照常
    bad = RoleAccess(True, scopes=(_sc("渠道", "美团"),))
    assert effective_expr("order", [bad, RoleAccess(True, scopes=(east,))]) == east_expr
    assert effective_expr("order", [bad]) == DENY_ALL
    # 前端 RowScope.op 只有 '=' / '∈'（roles.ts L12/L49）；'in' 视同 '∈'；其它 op → 这条 DENY（不当成不限制）
    east_in = {**east, "op": "∈", "values": ["华东", "华南"]}
    in_expr = {"field": "order_belongs_to_store.big_region", "op": "in", "value": ["华东", "华南"]}
    assert effective_expr("order", [RoleAccess(True, scopes=(east_in,))]) == in_expr
    assert effective_expr("order", [RoleAccess(True, scopes=({**east_in, "op": "in"},))]) == in_expr
    for op in ("≠", "!=", "not in", "∉", "like", "", None):
        neg = RoleAccess(True, scopes=({**east, "op": op},))
        assert effective_expr("order", [neg]) == DENY_ALL, op
        assert (
            effective_expr("product", [RoleAccess(True, scopes=({**_sc("品类", "生鲜"), "op": op},))])
            == DENY_ALL
        )
        assert effective_expr("order", [neg, RoleAccess(True, scopes=(st,))]) == store_expr


def test_effective_expr_product_same_rule():
    """商品和其它对象同一套 roles.ts:110 规则，没有「没配品类就放开」的特例。"""
    from app.common.row_filter import DENY_ALL
    from app.common.sql_guard import RoleAccess, effective_expr

    _gov()
    r05 = RoleAccess(True, default_deny=True)  # R-05/R-06/R-08：能看商品、没配商品范围
    dairy, fresh = _sc("品类", "乳品烘焙"), _sc("品类", "生鲜")
    assert effective_expr("product", [r05]) == DENY_ALL
    assert effective_expr("product", [RoleAccess(True)]) is None  # 非 defaultDeny、没配品类 → 不限制
    d = effective_expr("product", [RoleAccess(True, scopes=(dairy,))])
    f = effective_expr("product", [RoleAccess(True, scopes=(fresh,))])
    assert d not in (None, DENY_ALL)
    assert effective_expr("product", [r05, RoleAccess(True, scopes=(dairy,))]) == d
    both = [RoleAccess(True, scopes=(dairy,)), RoleAccess(True, scopes=(fresh,))]
    assert effective_expr("product", both) == {"or": [d, f]}
    assert effective_expr("product", [RoleAccess(True, scopes=(dairy, fresh))]) == {"or": [d, f]}


async def test_e2e_r05_alone_on_product_denies(biz, conn, monkeypatch):
    from app.common.sql_guard import RoleAccess

    async def q(sql):
        return (await run_guarded(conn, sql, _U())).scalar()

    _scoped(monkeypatch, {"product": [RoleAccess(True, default_deny=True)]})
    assert await q("select count(*) from product") == 0
    _scoped(monkeypatch, {"product": [RoleAccess(True)]})
    assert await q("select count(*) from product") == 12
    _scoped(
        monkeypatch,
        {
            "product": [
                RoleAccess(True, default_deny=True),
                RoleAccess(True, scopes=(_sc("品类", "乳品烘焙"),)),
            ]
        },
    )
    dairy = await biz.scalar(text("SELECT count(*) FROM product WHERE split_part(category, '/', 1) = '乳品'"))
    assert 0 < dairy < 12 and await q("select count(*) from product") == dairy


async def test_e2e_unmapped_scope_denies(biz, conn, monkeypatch):
    from app.common.sql_guard import RoleAccess

    async def q(sql):
        return (await run_guarded(conn, sql, _U())).scalar()

    # 商品上配了没映射的属性 → product 拒绝
    _scoped(monkeypatch, {"product": [_sc("品牌", "光明")]})
    assert await q("select count(*) from product") == 0
    # 订单范围全是没映射的属性 → 订单、订单行都拒绝
    _scoped(monkeypatch, {"order": [_sc("渠道", "美团")]})
    assert await q("select count(*) from biz_order") == 0
    assert await q("select count(*) from order_line") == 0
    # 和能映射的范围 OR 在一起：没映射那条不贡献行（绝不当成「不限制」）
    shop = await biz.scalar(
        text("SELECT s.name FROM biz_order o JOIN store s ON s.id = o.store_id ORDER BY s.code LIMIT 1")
    )
    _scoped(monkeypatch, {"order": [_sc("门店", shop), _sc("渠道", "美团")]})
    n_shop = await biz.scalar(
        text("SELECT count(*) FROM biz_order o JOIN store s ON s.id = o.store_id WHERE s.name = :n"),
        {"n": shop},
    )
    total = await biz.scalar(text("SELECT count(*) FROM biz_order"))
    assert 0 < n_shop < total and await q("select count(*) from biz_order") == n_shop
    # 会员范围：一期没有 member 表，查会员本身就拒绝；不影响别的对象
    _scoped(monkeypatch, {"member": [_sc("常去门店所属大区", "华东")], "store": [_sc("大区", "华东")]})
    with pytest.raises(SqlRejected):
        await q("select count(*) from member")
    assert await q("select count(*) from biz_order") == 0
    assert await q("select count(*) from store") > 0
    # R-06 门店店长（defaultDeny，订单.门店=上海徐汇店，商品没配范围）：
    # 订单 0 行（店名对不上，用户已定：预期如此），商品、门店拒绝
    r06 = {
        "order": [RoleAccess(True, default_deny=True, scopes=(_sc("门店", "上海徐汇店"),))],
        "product": [RoleAccess(True, default_deny=True)],
        "store": [RoleAccess(True, default_deny=True)],
    }
    _scoped(monkeypatch, r06)
    for t in ("biz_order", "order_line", "product", "store"):
        assert await q(f"select count(*) from {t}") == 0
