"""行级过滤表达式（基础层负责，本体小白）。

存储：jsonb（例如静态的 ROLE_OBJECT_SCOPE.row_filter、动态规则的作用范围），结构由这里的 Pydantic 模型定义：

    {"and": [
        {"field": "region", "op": "=", "valueFrom": "user.region"},
        {"or": [
            {"field": "format", "op": "in", "value": ["社区店", "前置仓"]},
            {"field": "store_id", "op": "=", "value": "SH0031"}
        ]}
    ]}

- 叶子 Condition：field + op + （value | valueFrom 二选一；is_null / not_null 两者都不要）
- valueFrom 只能取当前用户属性：`user.<attr>`（attr 来自 CurrentUser.attrs，PDE 负责填充）
- 组 Group：{"and": [...]} 或 {"or": [...]}，可嵌套，最多 MAX_DEPTH 层
- 空表达式 / null = 不限制（和前端“空=全部”一致）

转 SQL：`to_sql(expr, columns, user_attrs)`
- columns 是**白名单** {field: SQLAlchemy 列}，不在白名单里的字段直接报错；
- 值一律走绑定参数，不拼接字符串 → 无 SQL 注入面。

前端 mock 的 RowScope{objType, prop, op: '=' | '∈', values} 用 `from_row_scope()` 转换。
"""

from typing import Annotated, Any, Literal, Union

from pydantic import Field, model_validator
from sqlalchemy import ColumnElement, Select, and_, false, or_, true

from app.core.errors import AppError
from app.core.schema import ApiModel

MAX_DEPTH = 5
Op = Literal["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "like", "is_null", "not_null"]
_OP_ALIASES = {"∈": "in", "∉": "not_in", "≠": "!=", "≥": ">=", "≤": "<="}


class Condition(ApiModel):
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    op: Op
    value: Any = None
    value_from: str | None = Field(default=None, pattern=r"^user\.[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="before")
    @classmethod
    def _alias_ops(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("op") in _OP_ALIASES:
            data = {**data, "op": _OP_ALIASES[data["op"]]}
        return data

    @model_validator(mode="after")
    def _check_value(self) -> "Condition":
        unary = self.op in ("is_null", "not_null")
        has_value = self.value is not None
        has_from = self.value_from is not None
        if unary and (has_value or has_from):
            raise ValueError(f"{self.op} 不需要 value")
        if not unary and has_value == has_from:
            raise ValueError("value 和 valueFrom 必须且只能给一个")
        if self.op in ("in", "not_in") and has_value and not isinstance(self.value, list):
            raise ValueError("in / not_in 的 value 必须是数组")
        return self


class AndGroup(ApiModel):
    and_: list["Expr"] = Field(alias="and", min_length=1)


class OrGroup(ApiModel):
    or_: list["Expr"] = Field(alias="or", min_length=1)


Expr = Annotated[Union[Condition, AndGroup, OrGroup], Field(union_mode="left_to_right")]  # noqa: UP007
AndGroup.model_rebuild()
OrGroup.model_rebuild()


class RowFilterExpr(ApiModel):
    """jsonb 列的根类型。用法：RowFilterExpr.model_validate({"root": ...}) 或 parse_row_filter(jsonb)。"""

    root: Expr | None = None


def parse_row_filter(data: dict | None) -> Expr | None:
    if not data:
        return None
    expr = RowFilterExpr.model_validate({"root": data}).root
    if expr is not None and _depth(expr) > MAX_DEPTH:
        raise ValueError(f"行级过滤嵌套超过 {MAX_DEPTH} 层")
    return expr


def _depth(e: Expr) -> int:
    if isinstance(e, Condition):
        return 1
    children = e.and_ if isinstance(e, AndGroup) else e.or_
    return 1 + max(_depth(c) for c in children)


def to_sql(
    expr: Expr | dict | None, columns: dict[str, ColumnElement], user_attrs: dict[str, Any]
) -> ColumnElement[bool]:
    """表达式 → SQLAlchemy 条件。None = 不限制（true()）。"""
    if isinstance(expr, dict):
        expr = parse_row_filter(expr)
    if expr is None:
        return true()
    if isinstance(expr, AndGroup):
        return and_(*(to_sql(c, columns, user_attrs) for c in expr.and_))
    if isinstance(expr, OrGroup):
        return or_(*(to_sql(c, columns, user_attrs) for c in expr.or_))
    return _cond(expr, columns, user_attrs)


def _cond(c: Condition, columns: dict[str, ColumnElement], user_attrs: dict[str, Any]) -> ColumnElement:
    col = columns.get(c.field)
    if col is None:
        raise AppError(
            f"行级过滤字段 {c.field} 不在白名单", code="BAD_ROW_FILTER", details={"field": c.field}
        )
    if c.op == "is_null":
        return col.is_(None)
    if c.op == "not_null":
        return col.is_not(None)
    value = c.value
    if c.value_from:
        attr = c.value_from.split(".", 1)[1]
        if attr not in user_attrs:
            return false()  # 用户没有这个属性 → 什么都看不到（宁严勿宽）
        value = user_attrs[attr]
    if c.op in ("in", "not_in"):
        vals = value if isinstance(value, list | tuple | set) else [value]
        return col.in_(list(vals)) if c.op == "in" else col.not_in(list(vals))
    return {
        "=": lambda: col == value,
        "!=": lambda: col != value,
        ">": lambda: col > value,
        ">=": lambda: col >= value,
        "<": lambda: col < value,
        "<=": lambda: col <= value,
        "like": lambda: col.like(value),
    }[c.op]()


def from_row_scope(scope: dict[str, Any], field_of_prop: dict[str, str] | None = None) -> dict:
    """前端 RowScope {prop, op, values} → 表达式 jsonb。prop 是显示名时用 field_of_prop 映射到字段名。"""
    field = (field_of_prop or {}).get(scope["prop"], scope["prop"])
    values = list(scope["values"])
    if scope["op"] == "=" and len(values) == 1:
        return {"field": field, "op": "=", "value": values[0]}
    return {"field": field, "op": "in", "value": values}


def combine_or(exprs: list[dict | None]) -> dict | None:
    """一个用户多个角色：范围取并集（任一角色允许即可见）。任一为空（=全部）则整体不限制。"""
    if not exprs or any(not e for e in exprs):
        return None
    return exprs[0] if len(exprs) == 1 else {"or": exprs}


async def apply_row_filter(
    stmt: Select, user: Any, object_type: str, columns: dict[str, ColumnElement]
) -> Select:
    """基础层注入行级过滤：biz 等业务数据查询统一这样写，不要自己拼条件。

    stmt = await apply_row_filter(select(Store), user, "store", {"region": Store.region})
    """
    from app.common.auth import row_filter  # 避免循环导入

    expr = await row_filter(user, object_type)
    return stmt if expr is None else stmt.where(to_sql(expr, columns, user.attrs))
