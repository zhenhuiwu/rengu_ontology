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
- 单个角色的空表达式 / null = 该角色不限制（只有明确配置为「全部」的角色才会是空）；
  一个用户**没有任何**带范围的角色 → 什么都看不到（combine_or([]) = 拒绝，宁严勿宽）
- 跨对象一跳：field 写成 `<关系id>.<目标对象属性>`（如 `order_belongs_to_store.region`），
  关系必须来自**静态的关系定义**（LINKS：from / to / fromProp / toProp），用 link_paths_from_definitions()
  生成白名单；编译成 `EXISTS (SELECT 1 FROM 目标 WHERE 目标.toProp = 本表.fromProp AND 条件)`，值走绑定参数。
  多跳（两个点）或未声明的关系直接拒绝。

转 SQL：`to_sql(expr, columns, user_attrs)`
- columns 是**白名单** {field: SQLAlchemy 列}，不在白名单里的字段直接报错；
- 值一律走绑定参数，不拼接字符串 → 无 SQL 注入面。

前端 mock 的 RowScope{objType, prop, op: '=' | '∈', values} 用 `from_row_scope()` 转换。
"""

from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from pydantic import Field, model_validator
from sqlalchemy import ColumnElement, FromClause, Select, and_, exists, false, or_, select, true

from app.core.errors import AppError
from app.core.schema import ApiModel

MAX_DEPTH = 5
Op = Literal["=", "!=", "in", "not_in", ">", ">=", "<", "<=", "like", "is_null", "not_null"]
_OP_ALIASES = {"∈": "in", "∉": "not_in", "≠": "!=", "≥": ">=", "≤": "<="}


class Condition(ApiModel):
    # 本表字段，或一跳 `<关系id>.<目标属性>`；两个点（多跳）在校验时就拒绝
    field: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
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
        if self.op not in ("in", "not_in") and isinstance(self.value, list | dict):
            raise ValueError(f"{self.op} 的 value 必须是单个值（数组请用 in / not_in）")
        if self.op == "like" and has_value and not isinstance(self.value, str):
            raise ValueError("like 的 value 必须是字符串")
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


@dataclass(frozen=True)
class ObjectTable:
    """一个对象类型落在哪张表、属性 → 列（白名单）。"""

    table: FromClause
    columns: dict[str, ColumnElement]


@dataclass(frozen=True)
class LinkPath:
    """一跳关系（来自静态关系定义）：本表.local = 目标.target_key。"""

    link_id: str
    local: ColumnElement
    target: FromClause
    target_key: ColumnElement
    target_columns: dict[str, ColumnElement]


def link_paths_from_definitions(
    object_type: str, link_defs: list[dict[str, Any]], objects: dict[str, ObjectTable]
) -> dict[str, LinkPath]:
    """把静态的关系定义（LINKS 里 from == object_type 的那些）转成一跳白名单。

    link_defs 的每项至少有 id / from / to / fromProp / toProp（前端 mock LINKS 的字段）；
    objects 是对象类型 id → ObjectTable。关系两端的属性必须在对应对象的列白名单里。
    """
    out: dict[str, LinkPath] = {}
    src = objects.get(object_type)
    if src is None:
        raise ValueError(f"对象类型 {object_type} 没有登记表")
    for d in link_defs:
        if d.get("from") != object_type:
            continue
        dst = objects.get(d["to"])
        if dst is None:
            continue  # 目标对象没有登记表 → 这条关系不能用于行级过滤
        local, key = src.columns.get(d["fromProp"]), dst.columns.get(d["toProp"])
        if local is None or key is None:
            raise ValueError(f"关系 {d['id']} 的 fromProp/toProp 不在列白名单")
        out[d["id"]] = LinkPath(d["id"], local, dst.table, key, dst.columns)
    return out


def _bad(field: str, why: str) -> AppError:
    return AppError(f"行级过滤字段 {field} {why}", code="BAD_ROW_FILTER", details={"field": field})


def to_sql(
    expr: Expr | dict | None,
    columns: dict[str, ColumnElement],
    user_attrs: dict[str, Any],
    links: dict[str, LinkPath] | None = None,
) -> ColumnElement[bool]:
    """表达式 → SQLAlchemy 条件。None = 该角色不限制（true()）。links = 允许的一跳关系白名单。"""
    if isinstance(expr, dict):
        if is_deny_all(expr):
            return false()
        expr = parse_row_filter(expr)
    if expr is None:
        return true()
    if isinstance(expr, AndGroup):
        return and_(*(to_sql(c, columns, user_attrs, links) for c in expr.and_))
    if isinstance(expr, OrGroup):
        return or_(*(to_sql(c, columns, user_attrs, links) for c in expr.or_))
    if "." in expr.field:
        link_id, prop = expr.field.split(".", 1)
        link = (links or {}).get(link_id)
        if link is None:
            raise _bad(expr.field, "引用的关系没有在静态关系定义里声明（或不是一跳）")
        col = link.target_columns.get(prop)
        if col is None:
            raise _bad(expr.field, "不在目标对象的白名单")
        inner = _cond(expr, col, user_attrs)
        return exists(select(1).select_from(link.target).where(link.target_key == link.local, inner))
    col = columns.get(expr.field)
    if col is None:
        raise _bad(expr.field, "不在白名单")
    return _cond(expr, col, user_attrs)


def _cond(c: Condition, col: ColumnElement, user_attrs: dict[str, Any]) -> ColumnElement:
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
        if isinstance(value, list | tuple | set) and c.op not in ("in", "not_in"):
            if c.op != "=":
                raise _bad(c.field, f"的用户属性 {attr} 是多值，不能用 {c.op}")
            return col.in_(list(value))
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


DENY_ALL: dict = {"deny": True}  # 哨兵：什么都看不到
POSITIVE_OPS = frozenset({"=", "∈", "in"})  # 前端 RowScope.op（roles.ts L12/L49）


def valid_scope_values(values: Any) -> bool:
    """RowScope.values 必须是非空 list / tuple，元素都是非空字符串。

    裸字符串（会被拆成单字）、含 None / 空串 / 非字符串、空列表、缺字段 → 不合法，
    调用方返回 DENY_ALL（不 500）。
    """
    return (
        isinstance(values, (list, tuple))
        and len(values) > 0
        and all(isinstance(v, str) and v != "" for v in values)
    )


def valid_row_scope(scope: Any) -> bool:
    """一条 RowScope 结构上合法：是 dict；prop 是非空字符串；op 是正向 op；values 合法。

    缺 prop / 空串 / 非字符串（None、数字、列表…）、缺 op 或非正向 op、values 不合法 → False，
    调用方返回 DENY_ALL。
    """
    if not isinstance(scope, dict):
        return False
    prop = scope.get("prop")
    return (
        isinstance(prop, str)
        and prop.strip() != ""
        and scope.get("op") in POSITIVE_OPS
        and valid_scope_values(scope.get("values"))
    )


def from_row_scope(scope: dict[str, Any], field_of_prop: dict[str, str] | None = None) -> dict:
    """前端 RowScope {prop, op, values} → 表达式 jsonb。prop 是显示名时用 field_of_prop 映射到字段名。

    前端 RowScope.op 只有正向的 '=' 和 '∈'（roles.ts L12/L49；'in' 视同 '∈'）。
    其它 op（≠、not in、拼错的…）→ DENY_ALL（宁严勿宽；全 OR 合并只在正向 op 下成立）。
    结构不合法（见 valid_row_scope：缺 / 空 / 非字符串 prop、缺 op、values 不合法）→ DENY_ALL。
    """
    if not valid_row_scope(scope):
        return dict(DENY_ALL)
    field = (field_of_prop or {}).get(scope["prop"], scope["prop"])
    values = list(scope["values"])
    if scope["op"] == "=" and len(values) == 1:
        return {"field": field, "op": "=", "value": values[0]}
    return {"field": field, "op": "in", "value": values}


def combine_or(exprs: list[dict | None]) -> dict | None:
    """一个用户多个角色：范围取并集（任一角色允许即可见）。

    - 没有任何角色范围（[]）或全是 DENY_ALL → DENY_ALL（拒绝，不是“不限制”）；DENY_ALL 的角色不参与并集；
    - 某个角色明确配置为「全部」（传 None / {}）→ 整体不限制，返回 None。
    """
    if any(not e for e in exprs):
        return None
    allowed = [e for e in exprs if not is_deny_all(e)]  # 拒绝的角色不贡献可见范围
    if not allowed:
        return DENY_ALL
    return allowed[0] if len(allowed) == 1 else {"or": allowed}


def is_deny_all(expr: dict | None) -> bool:
    return isinstance(expr, dict) and expr.get("deny") is True


async def apply_row_filter(
    stmt: Select,
    user: Any,
    object_type: str,
    columns: dict[str, ColumnElement],
    links: dict[str, LinkPath] | None = None,
) -> Select:
    """基础层注入行级过滤：biz 等业务数据查询统一这样写，不要自己拼条件。

    stmt = await apply_row_filter(select(Store), user, "store", {"region": Store.region})
    links 由 link_paths_from_definitions(静态关系定义) 生成；不传则不允许跨对象字段。
    """
    from app.common.auth import row_filter  # 避免循环导入

    expr = await row_filter(user, object_type)
    if expr is None:
        return stmt
    if is_deny_all(expr):
        return stmt.where(false())
    return stmt.where(to_sql(expr, columns, user.attrs, links))
