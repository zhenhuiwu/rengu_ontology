"""问数 NL2SQL 网关的三条硬规则（api-index §1.4，已定）里骨架能给的部分。

1. `assert_single_select(sql)`：sqlglot 解析，只放行**单条只读 SELECT**。拒绝多语句、DDL/DML、
   `SELECT … INTO`、`FOR UPDATE/SHARE`、CTE 里夹带的 INSERT/UPDATE/DELETE、绑定参数占位符、
   TABLESAMPLE、public 以外的 schema、带 schema 的函数调用（`x.age()`、`pg_catalog.lower()`）、
   白名单以外的类型转换（只允许数值 / 文本 / 日期时间 / 布尔，拒 regclass、oid、自定义类型等）；
   函数走**白名单**（聚合、日期、字符串、数学、条件，见 `_ALLOWED_FUNCS`），
   其它一律 SQL_REJECTED（query_to_xml、pg_stat_file、current_setting、pg_advisory_lock 等）。
   执行用只读引擎 `app.db.session.get_qa_readonly_engine()`（连接时设 statement_timeout 和
   default_transaction_read_only=on；未配置直接报错，不回退主库）。
2. 身份只取我方会话：网关拿 `CurrentUserDep`，**不读请求里的任何用户 id**（见 app/common/auth.py）。
3. `inject_row_filters(sql, filters)`：把受控表替换成带行级条件的子查询
   `(SELECT * FROM public.t WHERE <谓词>) AS t`，不依赖上游 SQL 自带过滤。
   - 按 sqlglot scope 判断引用：只跳过**确实解析到 CTE** 的引用；CTE 内部引用的真表照样加过滤
     （`with store as (select * from store) select * from store` 里内层 store 会被过滤）；
   - 谓词 = `(sql, params)`，sql 里用 `$1..$n`（asyncpg / `exec_driver_sql` 的占位符），值走绑定参数，
     不拼进 SQL；多张表的参数合并后重新编号；谓词里的表都写成 `public.<表>`，不会被用户的同名 CTE 劫持；
   - None = 不限制；`DENY` = `("FALSE", [])`；不在 filters 也不在 allowed 里的表一律拒绝；
   - 不加过滤的表（谓词 None、allowed）也改写成 `public.<表>`，不依赖 search_path。

受控表登记：模块启动时 `register_governed(GovernedTable(...))`（例如 biz 登记 store、biz_order、order_line）。
- 继承上级行级权限：`GovernedTable(..., inherit=InheritVia(local, parent, parent_key))`，如 order_line 经
  order_id → biz_order 继承订单的谓词：`EXISTS (SELECT 1 FROM public.biz_order AS _rf_biz_order
  WHERE _rf_biz_order.id = order_line.order_id AND <biz_order 的谓词>)`（订单谓词本身再一跳到 store）。
  别名 `_rf_` 前缀，只在注入的子查询内部可见，不会和用户的别名冲突。
- 不需要行级的表要**显式**登记 `register_unrestricted(...)`；没登记的表一律拒绝（不是默认放行）。
- 范围值换名用 `value_alias`（product：「商品.品类」按 `split_part(category, '/', 1)` 过滤，值先换一级类目）。
- 前端角色 → 表达式：`effective_expr(object_type, [RoleAccess…])`，按 roles.ts:110-117 effectiveAccess 合并：
  跨角色——只看对该对象有查看权、且不是「defaultDeny 又没配该对象范围」的角色；其中任一角色没配范围 → 不限制，
  否则把这些角色的全部范围摊平取 OR（同一角色多条也 OR，[推断]，
  static-api §1.5@master 443c009 / roles.ts:114）；
  没映射的属性 / 没建表的对象 → 这条范围不贡献任何行（DENY，宁严勿宽）。
`row_filters_for(user)` 按 `auth.row_filter(user, object_type)` + `row_filter.to_sql` 编出各表谓词；
`run_guarded(conn, sql, user)` = 校验 + 注入 + 执行。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlalchemy import event, exists, select, text
from sqlalchemy.dialects.postgresql import asyncpg as _pg_asyncpg
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement, FromClause
from sqlalchemy.sql.util import ClauseAdapter
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from app.common.ontology import DEFAULT_ONTOLOGY_ID  # also registers the RLS after_begin hook first
from app.core.errors import Forbidden

DIALECT = "postgres"
ALLOWED_SCHEMAS = frozenset({"", "public"})

Predicate = tuple[str, list[Any]]  # ("<条件 SQL，占位符 $1..$n>", [参数])
DENY: Predicate = ("FALSE", [])

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Into,
    exp.Lock,
    exp.Placeholder,
    exp.Parameter,
    exp.TableSample,  # 不静默丢掉 TABLESAMPLE，直接拒
)

# 类型转换白名单（::、CAST()、TRY_CAST 都检查）：数值、文本、日期时间、布尔。其它（regclass、oid、json、
# bytea、数组、xml、带 schema 的自定义类型）一律 SQL_REJECTED。
_T = exp.DataType.Type
_ALLOWED_TYPES = frozenset(
    {
        _T.SMALLINT, _T.INT, _T.BIGINT, _T.DECIMAL, _T.FLOAT, _T.DOUBLE,
        _T.TEXT, _T.VARCHAR, _T.CHAR, _T.BPCHAR,
        _T.DATE, _T.TIME, _T.TIMETZ, _T.TIMESTAMP, _T.TIMESTAMPTZ, _T.INTERVAL,
        _T.BOOLEAN,
    }
)  # fmt: skip

# 函数白名单：sqlglot 的函数类名（同一函数的不同写法会归到同一个类，如 date_part → Extract）。
_ALLOWED_FUNCS = frozenset(
    {
        # 聚合
        "Count", "Sum", "Avg", "Min", "Max", "Median", "Variance", "VariancePop", "Stddev",
        "StddevSamp", "StddevPop", "GroupConcat", "ArrayAgg", "LogicalAnd", "LogicalOr",
        "PercentileCont", "PercentileDisc", "Mode",
        # 窗口
        "RowNumber", "Rank", "DenseRank", "Ntile", "Lead", "Lag", "FirstValue", "LastValue",
        "NthValue", "PercentRank", "CumeDist",
        # 日期
        "TimestampTrunc", "DateTrunc", "Extract", "TimeToStr", "StrToDate", "StrToTime",
        "CurrentTimestamp", "CurrentDate", "CurrentTime", "Localtimestamp", "Date", "DateAdd",
        "DateSub", "DateDiff", "JustifyDays", "JustifyHours", "JustifyInterval", "ToNumber",
        # 字符串
        "Upper", "Lower", "Initcap", "Substring", "Length", "Concat", "ConcatWs", "Trim", "Left",
        "Right", "Replace", "SplitPart", "StrPosition", "Pad", "Reverse", "Translate", "RegexpReplace",
        # 数学
        "Round", "Abs", "Floor", "Ceil", "Trunc", "Sign", "Ln", "Exp", "Log", "Sqrt", "Pow",
        # 条件 / 类型
        "Coalesce", "Nullif", "Greatest", "Least", "Case", "If", "Cast", "TryCast", "Array",
    }
)  # fmt: skip
# sqlglot 不认识（解析成 Anonymous）但允许的函数名
_ALLOWED_ANONYMOUS = frozenset({"age", "make_date", "make_interval", "every", "cardinality"})


class SqlRejected(Forbidden):
    code, message = "SQL_REJECTED", "生成的查询不符合只读规则"


def _func_allowed(node: exp.Func) -> bool:
    # 运算符在 sqlglot 里也是 Func 子类（AND/OR、~、^、EXISTS），不是函数调用
    if isinstance(node, (exp.Binary, exp.Connector, exp.Predicate)):
        return True
    if isinstance(node, exp.Anonymous):
        return node.name.lower() in _ALLOWED_ANONYMOUS
    return type(node).__name__ in _ALLOWED_FUNCS


def _interval_with_fields(this: object) -> bool:
    """`interval day`、`interval day to second`：sqlglot 解析成 DataType(this=Interval(unit=…))。

    只放行字段名（PDE 二审不阻塞项）。
    """
    if not isinstance(this, exp.Interval) or this.this is not None:
        return False
    unit = this.args.get("unit")
    parts = [unit.this, unit.expression] if isinstance(unit, exp.IntervalSpan) else [unit]
    return all(isinstance(p, exp.Var) for p in parts)


def _type_allowed(dt: exp.DataType) -> bool:
    if isinstance(dt, exp.IntervalSpan):  # `day to second` 本身也是 DataType 子类，只能出现在 interval 里
        return isinstance(dt.parent, exp.Interval) and all(
            isinstance(p, exp.Var) for p in (dt.this, dt.expression)
        )
    if _interval_with_fields(dt.this):
        return True
    if dt.this not in _ALLOWED_TYPES:
        return False
    # 只允许长度 / 精度参数（varchar(32)、numeric(10,2)）；不允许嵌套类型、带 schema 的名字
    return all(isinstance(c, exp.DataTypeParam) for c in dt.iter_expressions())


def _check_schema(table: exp.Table) -> None:
    if table.catalog or table.db.lower() not in ALLOWED_SCHEMAS:
        raise SqlRejected(f"只允许查询 public schema：{table.sql(dialect=DIALECT)}")


def assert_single_select(sql: str) -> exp.Expression:
    """解析并校验，返回 AST（后续给 inject_row_filters 用）。不合规抛 SqlRejected（403）。"""
    try:
        stmts = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SqlRejected("SQL 解析失败") from e
    if len(stmts) != 1:
        raise SqlRejected("只允许单条 SELECT")
    tree = stmts[0]
    if not isinstance(tree, exp.Query):
        raise SqlRejected("只允许 SELECT")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise SqlRejected(f"不允许 {node.key.upper()}")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            # x.age(now())、public.age(…)、pg_catalog.lower(…)：带 schema 的函数调用一律拒绝
            raise SqlRejected(f"不允许带 schema 的函数调用：{node.sql(dialect=DIALECT)}")
        if isinstance(node, exp.ObjectIdentifier) or (
            isinstance(node, exp.DataType) and not _type_allowed(node)
        ):  # regclass / oid / regproc / regtype 解析成 ObjectIdentifier
            raise SqlRejected(
                f"不允许转换成类型 {node.sql(dialect=DIALECT)}", details={"type": node.sql(dialect=DIALECT)}
            )
        if isinstance(node, exp.Func) and not _func_allowed(node):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            raise SqlRejected(f"不允许调用 {name.lower()}", details={"function": name.lower()})
        if isinstance(node, exp.Table):
            _check_schema(node)
    return tree


def _real_tables(tree: exp.Expression) -> list[exp.Table]:
    """按作用域找出真实表引用（解析到 CTE 的引用不算）。有作用域没覆盖到的表引用 → 拒绝。"""
    try:
        scopes = traverse_scope(tree)
    except Exception as e:
        raise SqlRejected("无法分析查询的作用域") from e
    real: dict[int, exp.Table] = {}
    cte_refs: set[int] = set()
    for scope in scopes:
        for t in scope.tables:
            if not t.db and t.name in scope.cte_sources:
                cte_refs.add(id(t))
            else:
                real[id(t)] = t
    for t in tree.find_all(exp.Table):
        if id(t) not in real and id(t) not in cte_refs:
            raise SqlRejected(f"无法确定表引用 {t.sql(dialect=DIALECT)} 的作用域")
    return list(real.values())


def _renumber(pred: exp.Expression, offset: int, n_params: int) -> None:
    for p in pred.find_all(exp.Parameter):
        i = int(p.this.name if isinstance(p.this, exp.Expression) else p.this)
        if not 1 <= i <= n_params:
            raise ValueError(f"谓词占位符 ${i} 超出参数个数 {n_params}")
        p.set("this", exp.Literal.number(i + offset))


def _parse_predicate(pred: Predicate) -> exp.Expression:
    text, _params = pred
    node = sqlglot.parse_one(text, read=DIALECT)
    for t in node.find_all(exp.Table):  # 谓词里的表固定到 public，不受用户 CTE 同名影响
        t.set("db", exp.to_identifier("public"))
    return node


def _pin_public(table: exp.Table) -> None:
    """不加过滤的表也写成 public.<表>，不依赖 search_path（search_path 被改也查不到别的 schema）。"""
    table.set("db", exp.to_identifier("public"))


def inject_row_filters(
    sql: str,
    filters: dict[str, Predicate | None],
    allowed: frozenset[str] = frozenset(),
) -> tuple[str, list[Any]]:
    """filters：受控表名 → 谓词 (sql, params)（None 不限制，DENY 拒绝全部）；allowed：不需要行级的表。

    返回 (改写后的 SQL, 绑定参数)，占位符 `$1..$n`，直接给 `conn.exec_driver_sql(sql, tuple(params))`。
    """
    tree = assert_single_select(sql)
    params: list[Any] = []
    for table in _real_tables(tree):
        name = table.name
        if name in filters:
            pred = filters[name]
            if pred is None:
                _pin_public(table)
                continue
            node = _parse_predicate(pred)
            _renumber(node, len(params), len(pred[1]))
            params.extend(pred[1])
            alias = table.alias_or_name
            sub = exp.select("*").from_(exp.to_table(f"public.{name}")).where(node)
            table.replace(exp.Subquery(this=sub, alias=exp.TableAlias(this=exp.to_identifier(alias))))
        elif name in allowed:
            _pin_public(table)
        else:
            raise SqlRejected(f"不允许查询表 {name}", details={"table": name})
    return tree.sql(dialect=DIALECT), params


# ---------------------------------------------------------------- 受控表登记


@dataclass(frozen=True)
class InheritVia:
    """本表经外键继承上级受控表的行级权限：本表.local = 上级.parent_key。"""

    local: ColumnElement
    parent: GovernedTable
    parent_key: ColumnElement


@dataclass(frozen=True)
class GovernedTable:
    """一张受行级过滤的表。columns / links 是 row_filter.to_sql 的列白名单和一跳关系。

    field_of_prop：权限范围里的中文属性名 → 字段
    （如 {"大区": "big_region"}，或一跳 "order_belongs_to_store.big_region"）。
    value_alias：权限范围里的中文属性名 → {范围值: 库里的值}（如 品类 → CATEGORY_SCOPE_ALIAS），
    `scope_expr` 换名，不在表里的值原样比。
    """

    table: FromClause
    object_type: str
    columns: dict[str, ColumnElement]
    links: dict[str, Any] = field(default_factory=dict)
    field_of_prop: dict[str, str] = field(default_factory=dict)
    inherit: InheritVia | None = None  # 设了就不用自己的 columns/links，权限按上级的 object_type 取
    value_alias: dict[str, Mapping[str, str]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.table.name  # type: ignore[attr-defined]

    @property
    def scope_object_type(self) -> str:
        """取行级表达式用的对象类型：继承的表用上级的（order_line → order）。"""
        return self.inherit.parent.scope_object_type if self.inherit else self.object_type


_GOVERNED: dict[str, GovernedTable] = {}
_UNRESTRICTED: set[str] = set()


def register_governed(*tables: GovernedTable) -> None:
    for g in tables:
        if g.name in _UNRESTRICTED:
            raise ValueError(f"表 {g.name} 已登记为不限制，不能再登记为受控")
        # scope_expr_for_object 按 object_type 找表（取第一个），所以一个对象类型只能对应一张受控表
        clash = next(
            (n for n, t in _GOVERNED.items() if t.object_type == g.object_type and n != g.name), None
        )
        if clash:
            raise ValueError(f"对象类型 {g.object_type} 已由表 {clash} 登记，不能再登记表 {g.name}")
        _GOVERNED[g.name] = g


def register_unrestricted(*names: str) -> None:
    """显式登记不需要行级过滤的表（维度表，如 product）。"""
    for n in names:
        if n in _GOVERNED:
            raise ValueError(f"表 {n} 已登记为受控，不能再登记为不限制")
        _UNRESTRICTED.add(n)


# ontology_id -> (loaded_at, object type codes defined in that ontology). Physical tables are registered once
# (they carry ontology_id + RLS); a workspace governs a table only when it defines the table's object type.
_BOUND: dict[int, tuple[float, frozenset[str]]] = {}
_BOUND_TTL = 30.0
_BOUND_SQL = text("SELECT code FROM object_type WHERE ontology_id = :ontology_id")


def _valid_ontology(ontology_id: Any) -> bool:
    return type(ontology_id) is int and ontology_id > 0


def _store_bound(ontology_id: int, codes: Any) -> None:
    _BOUND[ontology_id] = (time.monotonic(), frozenset(codes))


def _bound_stale(ontology_id: int) -> bool:
    hit = _BOUND.get(ontology_id)
    return hit is None or time.monotonic() - hit[0] > _BOUND_TTL


def _resolve(codes: frozenset[str]) -> dict[str, GovernedTable]:
    def bound(g: GovernedTable) -> bool:
        # Inherited tables (order_line → biz_order) carry no scope of their own: bound with their parent.
        return bound(g.inherit.parent) if g.inherit else g.object_type in codes

    return {name: g for name, g in _GOVERNED.items() if bound(g)}


def governed_tables(ontology_id: int | None = None) -> dict[str, GovernedTable]:
    """Governed tables of one ontology workspace (no argument = the whole physical registry).

    Resolved from the workspace's own object types (loaded per transaction by `load_governed_tables` /
    the after_begin hook, refreshed on object_type flushes). Before anything is loaded the legacy rule
    applies (workspace 1 = everything registered, others = none), so the result is never wider than before.
    """
    if ontology_id is None:
        return dict(_GOVERNED)
    if not _valid_ontology(ontology_id):
        return {}
    hit = _BOUND.get(ontology_id)
    if hit is None:
        return dict(_GOVERNED) if ontology_id == DEFAULT_ONTOLOGY_ID else {}
    return _resolve(hit[1])


async def load_governed_tables(session: Any, ontology_id: int | None = None) -> dict[str, GovernedTable]:
    """Reload the current (or given) workspace's object type codes and return its governed tables."""
    oid = ontology_id if ontology_id is not None else session.info.get("ontology_id")
    if not _valid_ontology(oid):
        return {}
    rows = await session.execute(_BOUND_SQL, {"ontology_id": oid})
    _store_bound(oid, rows.scalars())
    return governed_tables(oid)


@event.listens_for(Session, "after_begin")
def _load_bound_on_begin(session, transaction, connection):
    oid = session.info.get("ontology_id")
    if _valid_ontology(oid) and _bound_stale(oid):
        _store_bound(oid, connection.execute(_BOUND_SQL, {"ontology_id": oid}).scalars())


@event.listens_for(Session, "after_flush")
def _reload_bound_on_object_type_change(session, flush_context):
    oid = session.info.get("ontology_id")
    if not _valid_ontology(oid):
        return
    changed = (*session.new, *session.dirty, *session.deleted)
    if any(getattr(o, "__tablename__", None) == "object_type" for o in changed):
        _store_bound(oid, session.connection().execute(_BOUND_SQL, {"ontology_id": oid}).scalars())


def unrestricted_tables() -> frozenset[str]:
    return frozenset(_UNRESTRICTED)


_PG = _pg_asyncpg.dialect()


def compile_predicate(g: GovernedTable, expr: dict | None, user_attrs: dict[str, Any]) -> Predicate | None:
    """行级表达式 → 谓词 (sql, params)，占位符 $n（和 exec_driver_sql 一致）。None = 不限制。"""
    from app.common.row_filter import is_deny_all

    if expr is None:
        return None
    if is_deny_all(expr):
        return DENY
    cond = _condition(g, expr, user_attrs)
    compiled = (
        select(1)
        .select_from(g.table)
        .where(cond)
        .compile(dialect=_PG, compile_kwargs={"render_postcompile": True})
    )
    where = sqlglot.parse_one(str(compiled), read=DIALECT).args["where"].this
    names: Sequence[str] = compiled.positiontup or ()
    return where.sql(dialect=DIALECT), [compiled.params[n] for n in names]


def scope_expr(g: GovernedTable, scopes: Sequence[dict[str, Any]]) -> dict | None:
    """前端 RowScope 列表 {prop, op, values} → 行级表达式（多条之间 OR）。

    属性名按 field_of_prop 换成字段，取值按 value_alias 换名（不在表里的原样保留）；
    多条范围 OR **[推断]**（static-api §1.5@master 443c009 / roles.ts:114：同一角色多条也 OR；
    mock 里没有这种角色）。
    **没映射的属性 / 非正向 op → 这条是 DENY_ALL**（宁严勿宽，绝不忽略成「不限制」）：
    OR 里它不贡献行，全都这样 → DENY_ALL。
    空列表 → None（不限制）。多个角色用 effective_expr 合并。
    """
    from app.common.row_filter import DENY_ALL, combine_or, from_row_scope, valid_row_scope

    parts: list[dict] = []
    for sc in scopes:
        # 先查结构（prop 可能是列表等不可哈希的值，不能直接拿去查 field_of_prop）
        if not valid_row_scope(sc) or sc["prop"] not in g.field_of_prop:
            parts.append(dict(DENY_ALL))
            continue
        prop = sc["prop"]
        alias = g.value_alias.get(prop, {})
        parts.append(
            from_row_scope({**sc, "values": [alias.get(v, v) for v in sc["values"]]}, g.field_of_prop)
        )
    if not parts:
        return None
    return combine_or(parts)


def scope_expr_for_object(object_type: str, scopes: Sequence[dict[str, Any]]) -> dict | None:
    """按对象类型找受控表再 scope_expr。

    对象类型没有登记受控表（一期的会员、库存快照）且配了范围 → DENY_ALL。
    """
    from app.common.row_filter import DENY_ALL

    g = next((t for t in _GOVERNED.values() if t.object_type == object_type), None)
    if g is None:
        return dict(DENY_ALL) if scopes else None
    return scope_expr(g, scopes)


@dataclass(frozen=True)
class RoleAccess:
    """用户的一个角色在某个对象上的权限（PDE 的 row_filter 从角色表组装）。"""

    view: bool  # 该角色对该对象有查看权（roles.ts objs[].view）
    default_deny: bool = False  # roles.ts Role.defaultDeny
    scopes: tuple[dict[str, Any], ...] = ()  # 该角色在**该对象**上的 RowScope {prop, op, values}


def effective_expr(object_type: str, roles: Sequence[RoleAccess]) -> dict | None:
    """一个用户在某对象上的有效行级表达式。

    照前端 effectiveAccess（roles.ts:110-117，static-api §1.5@master 443c009）：

    1. 只算 granted 角色：有查看权，且不是「defaultDeny 又没配该对象范围」（roles.ts:110）；没有 → DENY_ALL；
    2. granted 里任一角色没配该对象范围 → None（不限制）；
    3. 否则 granted 角色的全部范围摊平后 OR（角色内、角色间都 OR，角色内 [推断]，
       static-api §1.5@master 443c009）；
       映射不了的范围不贡献行，全都映射不了 → DENY_ALL。
    """
    from app.common.row_filter import DENY_ALL

    granted = [r for r in roles if r.view and not (r.default_deny and not r.scopes)]
    if not granted:
        return dict(DENY_ALL)
    if any(not r.scopes for r in granted):
        return None
    return scope_expr_for_object(object_type, [sc for r in granted for sc in r.scopes])


_ALIAS_PREFIX = "_rf_"


def _condition(g: GovernedTable, expr: dict, user_attrs: dict[str, Any]) -> ColumnElement[bool]:
    from app.common.row_filter import to_sql

    via = g.inherit
    if via is None:
        return to_sql(expr, g.columns, user_attrs, g.links)
    parent = via.parent
    alias = parent.table.alias(_ALIAS_PREFIX + parent.name)
    inner = ClauseAdapter(alias).traverse(_condition(parent, expr, user_attrs))
    key = alias.c[via.parent_key.key]
    return exists(select(1).select_from(alias).where(key == via.local, inner))


async def row_filters_for(user: Any) -> dict[str, Predicate | None]:
    from app.common.auth import row_filter

    ontology_id = getattr(user, "ontology_id", None)
    governed = governed_tables(ontology_id) if _valid_ontology(ontology_id) else {}
    if not governed:
        raise SqlRejected("当前本体工作空间尚未绑定可执行的受控数据源")
    out: dict[str, Predicate | None] = {}
    for name, g in governed.items():
        pred = compile_predicate(g, await row_filter(user, g.scope_object_type), user.attrs)
        clause, values = pred if pred is not None else ("TRUE", [])
        out[name] = (f"({clause}) AND {name}.ontology_id = ${len(values) + 1}", [*values, ontology_id])
    return out


async def run_guarded(conn: Any, sql: str, user: Any, allowed: frozenset[str] = frozenset()) -> Any:
    """校验 + 注入行级条件 + 执行。

    conn = 只读引擎的 AsyncConnection（`get_qa_readonly_engine().connect()`）。
    """
    if allowed or unrestricted_tables():
        raise SqlRejected("物理表必须登记工作空间绑定，不能绕过本体边界")
    final, params = inject_row_filters(sql, await row_filters_for(user))
    return await conn.exec_driver_sql(final, tuple(params))
