"""问数 NL2SQL 网关的三条硬规则（api-index §1.4，已定）里骨架能给的部分。

1. `assert_single_select(sql)`：sqlglot 解析，只放行**单条只读 SELECT**。拒绝多语句、DDL/DML、
   `SELECT … INTO`、`FOR UPDATE/SHARE`、CTE 里夹带的 INSERT/UPDATE/DELETE、以及危险函数
   （pg_sleep、dblink、lo_*、pg_read_file、set_config 等）。执行仍须用只读库账号
   （`RENGU_QA_READONLY_DATABASE_URL`，库层面 `default_transaction_read_only=on`、只授权业务表），
   这里只是第一道闸。
2. 身份只取我方会话：网关拿 `CurrentUserDep`，**不读请求里的任何用户 id**（见 app/common/auth.py）。
3. `inject_row_filters(sql, filters)`：把受控表替换成带行级条件的子查询
   `(SELECT * FROM t WHERE <谓词>) AS t`，不依赖上游 SQL 自带过滤。谓词由调用方用
   `row_filter(user, object_type)` + `to_sql(...)` 生成后编译成 SQL 文本；None = 不限制，
   "FALSE" = DENY_ALL（defaultDeny 没配范围）。不在 filters 也不在 allowed 里的表一律拒绝。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from app.core.errors import Forbidden

DIALECT = "postgres"

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
)
_FORBIDDEN_FUNCS = {
    "pg_sleep",
    "dblink",
    "dblink_exec",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "set_config",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "lo_import",
    "lo_export",
    "nextval",
    "setval",
}


class SqlRejected(Forbidden):
    code, message = "SQL_REJECTED", "生成的查询不符合只读规则"


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
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _FORBIDDEN_FUNCS or name.startswith("lo_"):
                raise SqlRejected(f"不允许调用 {name}")
    return tree


def inject_row_filters(
    sql: str,
    filters: dict[str, str | None],
    allowed: frozenset[str] = frozenset(),
) -> str:
    """filters：受控表名 → 谓词 SQL（None 不限制，"FALSE" 拒绝全部）；allowed：不需要行级的表（维表等）。"""
    tree = assert_single_select(sql)
    cte_names = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    for table in list(tree.find_all(exp.Table)):
        name = table.name
        if name in cte_names and not table.db:
            continue
        if name in filters:
            pred = filters[name]
            if pred is None:
                continue
            alias = table.alias_or_name
            sub = exp.select("*").from_(exp.to_table(name)).where(sqlglot.parse_one(pred, read=DIALECT))
            table.replace(exp.Subquery(this=sub, alias=exp.TableAlias(this=exp.to_identifier(alias))))
        elif name not in allowed:
            raise SqlRejected(f"不允许查询表 {name}")
    return tree.sql(dialect=DIALECT)
