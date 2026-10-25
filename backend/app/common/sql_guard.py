"""问数 NL2SQL 网关的三条硬规则（api-index §1.4，已定）里骨架能给的部分。

1. `assert_single_select(sql)`：sqlglot 解析，只放行**单条只读 SELECT**。拒绝多语句、DDL/DML、
   `SELECT … INTO`、`FOR UPDATE/SHARE`、CTE 里夹带的 INSERT/UPDATE/DELETE、绑定参数占位符、
   public 以外的 schema；函数走**白名单**（聚合、日期、字符串、数学、条件，见 `_ALLOWED_FUNCS`），
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
   - None = 不限制；`DENY` = `("FALSE", [])`；不在 filters 也不在 allowed 里的表一律拒绝。

受控表登记：模块启动时 `register_governed(GovernedTable(...))`（例如 biz 登记 store、biz_order）。
`row_filters_for(user)` 按 `auth.row_filter(user, object_type)` + `row_filter.to_sql` 编出各表谓词；
`run_guarded(conn, sql, user)` = 校验 + 注入 + 执行。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import asyncpg as _pg_asyncpg
from sqlalchemy.sql import ColumnElement, FromClause
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

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
)

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
                continue
            node = _parse_predicate(pred)
            _renumber(node, len(params), len(pred[1]))
            params.extend(pred[1])
            alias = table.alias_or_name
            sub = exp.select("*").from_(exp.to_table(f"public.{name}")).where(node)
            table.replace(exp.Subquery(this=sub, alias=exp.TableAlias(this=exp.to_identifier(alias))))
        elif name not in allowed:
            raise SqlRejected(f"不允许查询表 {name}", details={"table": name})
    return tree.sql(dialect=DIALECT), params


# ---------------------------------------------------------------- 受控表登记


@dataclass(frozen=True)
class GovernedTable:
    """一张受行级过滤的表。columns / links 是 row_filter.to_sql 的列白名单和一跳关系。

    field_of_prop：权限范围里的中文属性名 → 字段
    （如 {"大区": "big_region"}，或一跳 "order_belongs_to_store.big_region"）。
    """

    table: FromClause
    object_type: str
    columns: dict[str, ColumnElement]
    links: dict[str, Any] = field(default_factory=dict)
    field_of_prop: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.table.name  # type: ignore[attr-defined]


_GOVERNED: dict[str, GovernedTable] = {}


def register_governed(*tables: GovernedTable) -> None:
    for g in tables:
        _GOVERNED[g.name] = g


def governed_tables() -> dict[str, GovernedTable]:
    return dict(_GOVERNED)


_PG = _pg_asyncpg.dialect()


def compile_predicate(g: GovernedTable, expr: dict | None, user_attrs: dict[str, Any]) -> Predicate | None:
    """行级表达式 → 谓词 (sql, params)，占位符 $n（和 exec_driver_sql 一致）。None = 不限制。"""
    from app.common.row_filter import is_deny_all, to_sql

    if expr is None:
        return None
    if is_deny_all(expr):
        return DENY
    cond = to_sql(expr, g.columns, user_attrs, g.links)
    compiled = (
        select(1)
        .select_from(g.table)
        .where(cond)
        .compile(dialect=_PG, compile_kwargs={"render_postcompile": True})
    )
    where = sqlglot.parse_one(str(compiled), read=DIALECT).args["where"].this
    names: Sequence[str] = compiled.positiontup or ()
    return where.sql(dialect=DIALECT), [compiled.params[n] for n in names]


async def row_filters_for(user: Any) -> dict[str, Predicate | None]:
    from app.common.auth import row_filter

    out: dict[str, Predicate | None] = {}
    for name, g in _GOVERNED.items():
        out[name] = compile_predicate(g, await row_filter(user, g.object_type), user.attrs)
    return out


async def run_guarded(conn: Any, sql: str, user: Any, allowed: frozenset[str] = frozenset()) -> Any:
    """校验 + 注入行级条件 + 执行。

    conn = 只读引擎的 AsyncConnection（`get_qa_readonly_engine().connect()`）。
    """
    final, params = inject_row_filters(sql, await row_filters_for(user), allowed)
    return await conn.exec_driver_sql(final, tuple(params))
