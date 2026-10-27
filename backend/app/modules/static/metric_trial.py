"""Execute only explicitly bound metric definitions; never infer missing business semantics."""

from copy import deepcopy
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

import sqlglot
from pydantic import Field, model_validator
from sqlalchemy import Date, DateTime, Numeric, cast, func, literal, select
from sqlalchemy.dialects import postgresql
from sqlglot import exp

from app.common.auth import CurrentUser, check_func, resolve_user
from app.common.context import ViewContext
from app.common.sql_guard import governed_tables, run_guarded
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.core.schema import ApiModel, _fmt_dt
from app.modules.static.models import ChangeRequest, CrItem, CRStatus, CrTarget, Metric, ObjectType
from app.modules.static.permissions import check_cr
from app.modules.static.schemas import ItemIn
from app.modules.static.trials import invalid, valid_context
from app.modules.versioning.repo import ReleaseRepo


class MetricTrialIn(ApiModel):
    cr_id: str | None = Field(None, min_length=1, max_length=64)
    range: Literal["30d"] = "30d"

    @model_validator(mode="after")
    def valid(self):
        ItemIn.jsonb_text(self.model_dump())
        return self


def unavailable(note: str) -> AppError:
    return AppError(note, code="METRIC_TRIAL_UNAVAILABLE", status_code=422)


async def metric_definition(session, code: str, user: CurrentUser, ver=None, cr_id=None) -> dict:
    if ver:
        release = await ReleaseRepo(session).by_ver_or_404(ver)
        metric = deepcopy(((release.snapshot or {}).get("metrics") or {}).get(code))
    else:
        row = await session.scalar(select(Metric).where(Metric.code == code))
        metric = (
            {
                "id": code,
                "name": row.display_name,
                "version": row.version,
                "formula": row.formula,
                "status": row.status.value,
                "def": deepcopy(row.def_rows or []),
            }
            if row
            else None
        )
    if not cr_id:
        if metric is None:
            raise NotFound("指标不存在于所选版本")
        if metric.get("status") != "已发布":
            raise unavailable("指标尚未发布，请在已保存的变更单草稿中试算")
        return metric
    if ver:
        raise unavailable("历史版本不能叠加当前变更单")
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_id))
    if cr is None:
        raise NotFound("变更单不存在")
    await check_cr(session, cr, user)
    if cr.status == CRStatus.published:
        raise unavailable("已发布变更单不能叠加到当前试算")
    target = await session.scalar(
        select(CrTarget).where(
            CrTarget.cr_id == cr.id, CrTarget.target_kind == "metric", CrTarget.target_id == code
        )
    )
    if target is None:
        raise NotFound("变更单未登记该指标")
    if metric is None:
        if not target.is_new:
            raise NotFound("指标没有已发布基线，且目标未标记为新建")
        metric = {"id": code, "name": target.target_name or code, "def": []}
    rows = await session.scalars(
        select(CrItem)
        .where(
            CrItem.cr_id == cr.id,
            CrItem.target_kind == "metric",
            CrItem.target_id == code,
            CrItem.section == "def",
        )
        .order_by(CrItem.seq, CrItem.id)
    )
    for item in rows:
        if item.item_key in ("name", "formula", "version"):
            metric[item.item_key] = None if item.op == "delete" else item.after
    return metric


class BoundFormula:
    """Small SQL-expression subset compiled to SQLAlchemy, with no user SQL execution."""

    def __init__(self, objects):
        self.objects = objects
        self.object = None
        self.governed = None
        self.aggregates = 0
        self.count_times = {}

    def bind_object(self, name):
        matches = [o for o in self.objects if name in (o.code, o.api_name, o.display_name)]
        if len(matches) != 1:
            raise unavailable(f"对象「{name}」不存在或含义不唯一")
        obj = matches[0]
        if obj.status.value != "已发布":
            raise unavailable(f"对象「{name}」尚未发布")
        if self.object is not None and self.object.id != obj.id:
            raise unavailable("当前试算只支持单个受治理对象，跨对象口径尚无可执行连接定义")
        gov = next((g for g in governed_tables().values() if g.object_type == obj.code), None)
        if gov is None:
            raise unavailable(f"对象「{name}」尚未绑定受治理业务表")
        self.object, self.governed = obj, gov
        return gov

    def column(self, node):
        if not isinstance(node, exp.Column) or node.db or node.catalog:
            raise unavailable("公式中的属性引用不合法")
        if node.table:
            self.bind_object(node.table)
        if self.object is None:
            raise unavailable("属性引用必须明确所属对象")
        props = [p for p in self.object.properties if node.name in (p.api_name, p.display_name)]
        if len(props) != 1:
            raise unavailable(f"属性「{node.name}」不存在或含义不唯一")
        prop = props[0]
        if prop.is_sensitive or prop.visibility == "hidden":
            raise unavailable(f"属性「{node.name}」不能用于试算")
        if not prop.backing_column or prop.backing_column not in self.governed.table.c:
            raise unavailable(f"属性「{node.name}」尚未绑定真实业务列")
        return self.governed.table.c[prop.backing_column]

    def expression(self, node):
        if isinstance(node, exp.Paren):
            return self.expression(node.this)
        if isinstance(node, (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max)):
            self.aggregates += 1
            if isinstance(node, exp.Count) and isinstance(node.this, exp.Column) and not node.this.table:
                gov = self.bind_object(node.this.name)
                return func.count(gov.table.c.id)
            col = self.column(node.this)
            if isinstance(node, exp.Count) and isinstance(col.type, (Date, DateTime)):
                self.count_times[col.key] = (f"{self.object.code}.{node.this.name}", col)
            if not isinstance(node, exp.Count) and not isinstance(col.type, Numeric):
                from sqlalchemy import Integer

                if not isinstance(col.type, Integer):
                    raise unavailable("聚合属性必须绑定数值列")
            return {
                exp.Sum: func.sum,
                exp.Count: func.count,
                exp.Avg: func.avg,
                exp.Min: func.min,
                exp.Max: func.max,
            }[type(node)](col)
        if isinstance(node, exp.Literal) and not node.is_string:
            value = Decimal(node.this)
            if not value.is_finite() or abs(value) > Decimal("1e20"):
                raise unavailable("公式常量超过可试算范围")
            return literal(value)
        if isinstance(node, exp.Neg):
            return -self.expression(node.this)
        if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
            left, right = self.expression(node.this), self.expression(node.expression)
            if isinstance(node, exp.Add):
                return left + right
            if isinstance(node, exp.Sub):
                return left - right
            if isinstance(node, exp.Mul):
                return left * right
            return left / func.nullif(right, 0)
        raise unavailable("公式尚不能确定执行含义；支持单对象SUM/COUNT/AVG/MIN/MAX及四则运算")

    def predicate(self, node):
        if isinstance(node, exp.Paren):
            return self.predicate(node.this)
        if isinstance(node, exp.And):
            return self.predicate(node.this) & self.predicate(node.expression)
        if not isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            raise unavailable("默认过滤尚不能确定执行含义；支持显式属性与标量比较")
        col = self.column(node.this)
        value = node.expression
        if isinstance(value, (exp.Literal, exp.Boolean)):
            raw = value.this
        elif isinstance(value, exp.Column) and not value.table:
            raw = value.name
        else:
            raise unavailable("过滤值必须是明确的标量")
        from app.modules.static.explorer import scalar_filter

        if raw in ("是", "否") and col.type.python_type is bool:
            raw = raw == "是"
        converted = scalar_filter(col, raw, "formula")
        if isinstance(node, exp.EQ):
            return col == converted
        if isinstance(node, exp.NEQ):
            return col != converted
        if isinstance(node, exp.GT):
            return col > converted
        if isinstance(node, exp.GTE):
            return col >= converted
        if isinstance(node, exp.LT):
            return col < converted
        return col <= converted


def parse_formula(value, *, normalize_separators=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise unavailable("没有可执行的指标公式")
    try:
        replacements = str.maketrans(
            {"−": "-", "≠": "<>", **({"；": " AND ", ";": " AND "} if normalize_separators else {})}
        )
        parts, cursor = [], 0
        for token in sqlglot.Dialect.get_or_raise("postgres").tokenize(value):
            if token.token_type.name.endswith("STRING") or token.token_type.name == "IDENTIFIER":
                parts.extend(
                    (value[cursor : token.start].translate(replacements), value[token.start : token.end + 1])
                )
                cursor = token.end + 1
        parts.append(value[cursor:].translate(replacements))
        trees = sqlglot.parse("SELECT " + "".join(parts), read="postgres")
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, RecursionError):
        raise unavailable("公式包含尚不支持的自然语言或语法，无法可靠试算") from None
    if (
        len(trees) != 1
        or not isinstance(trees[0], exp.Select)
        or len(trees[0].expressions) != 1
        or any(
            value is not None for key, value in trees[0].args.items() if key not in ("expressions", "where")
        )
    ):
        raise unavailable("指标公式不能包含查询、子查询、表或多条语句")
    if sum(1 for _ in trees[0].walk()) > 128:
        raise unavailable("公式过于复杂，请拆分口径后再试算")
    return trees[0]


async def evaluate_metric(
    session,
    user: CurrentUser,
    code: str,
    *,
    ver=None,
    cr_id=None,
    days=30,
    period="day",
    at=None,
    timezone=None,
    object_kind=None,
) -> dict:
    """Public evaluator: exact saved definition + governed live rows, inclusive local calendar days."""
    check_func(user, "metrics", "view")
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 93:
        raise unavailable("试算范围须为1至93天")
    if period not in ("day", "week", "month"):
        raise unavailable("试算分组仅支持日、周或月")
    metric = await metric_definition(session, code, user, ver, cr_id)
    # Older snapshots omit execution metadata. Do not borrow today's mappings/metadata for history.
    if ver:
        raise unavailable("该历史快照未保存完整执行映射，不能借用当前映射试算")
    objects = list(await session.scalars(select(ObjectType)))
    bound = BoundFormula(objects)
    tree = parse_formula(metric.get("formula"))
    expression = bound.expression(tree.expressions[0])
    if not bound.aggregates or bound.object is None:
        raise unavailable("公式未绑定可聚合的真实业务对象")
    metadata = {r.get("k"): r.get("v") for r in metric.get("def", []) if isinstance(r, dict)}
    time_field = metadata.get("时间字段")
    if not time_field and len(bound.count_times) == 1:
        # Counting an explicit date property already states its event time; do not choose a business date.
        time_field, temporal = next(iter(bound.count_times.values()))
    else:
        if not isinstance(time_field, str) or not time_field.strip():
            raise unavailable(
                "指标没有明确的时间字段；可在公式中显式COUNT唯一日期属性，不能默认选下单或出库时间"
            )
        try:
            time_node = sqlglot.parse_one(time_field, read="postgres")
        except (sqlglot.errors.ParseError, RecursionError):
            raise unavailable("指标时间字段不是明确的对象属性") from None
        temporal = bound.column(time_node)
    if not isinstance(temporal.type, (Date, DateTime)):
        raise unavailable("指标时间字段没有绑定日期或时间列")
    conds = []
    if tree.args.get("where"):
        conds.append(bound.predicate(tree.args["where"].this))
    default_filter = metadata.get("默认过滤")
    if default_filter:
        if not isinstance(default_filter, str):
            raise unavailable("默认过滤不是明确的文本表达式")
        filters = parse_formula("1 WHERE " + default_filter, normalize_separators=True)
        conds.append(bound.predicate(filters.args["where"].this))
    tz = ZoneInfo(timezone or get_settings().display_tz)
    cutoff_at = at or clock.now()
    if cutoff_at.tzinfo is None:
        raise unavailable("指标计算时间必须带时区")
    end = cutoff_at.astimezone(tz).date()
    start = end - timedelta(days=days - 1)
    if isinstance(temporal.type, DateTime):
        day = cast(temporal.op("AT TIME ZONE")(str(tz)), Date)
        lo, hi = (
            datetime.combine(start, time.min, tz),
            datetime.combine(end + timedelta(days=1), time.min, tz),
        )
    else:
        day, lo, hi = temporal, start, end + timedelta(days=1)
    if period in ("month", "week"):
        day = cast(func.date_trunc(period, day), Date)
    grouping = []
    if object_kind:
        if bound.object.code != object_kind or "code" not in bound.governed.table.c:
            raise unavailable("指标没有明确的规则对象分组映射，不能把汇总值分摊给各对象")
        grouping = [bound.governed.table.c.code]
    if at is not None and isinstance(temporal.type, DateTime):
        conds.append(temporal <= cutoff_at)
    stmt = (
        select(day.label("date"), expression.label("value"), func.max(temporal).label("cutoff"), *grouping)
        .select_from(bound.governed.table)
        .where(temporal >= lo, temporal < hi, *conds)
        .group_by(day, *grouping)
        .order_by(day)
    )
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    # Shared SQL guard supplies inherited row scopes, including order_line -> order -> store.
    rows = (await run_guarded(await session.connection(), sql, user)).mappings().all()
    values = [
        {
            "date": str(row["date"]),
            "value": str(row["value"]) if row["value"] is not None else None,
            **({"objectCode": row["code"]} if grouping else {}),
        }
        for row in rows
    ]
    cutoff = max((row["cutoff"] for row in rows if row["cutoff"] is not None), default=None)
    return {
        "id": code,
        "version": metric.get("version") or "草稿",
        "formula": metric["formula"],
        "rows": values,
        "dataCutoff": _fmt_dt(cutoff) if isinstance(cutoff, datetime) else str(cutoff) if cutoff else None,
        "scope": f"{start} 至 {end} · 时间字段 {time_field} · 当前用户可见数据",
        "crId": cr_id,
    }


async def trial_metric(session, ctx: ViewContext, user: CurrentUser, code: str, body: MetricTrialIn):
    valid_context(ctx)
    try:
        ItemIn.jsonb_text(code)
    except ValueError:
        raise invalid("id", "指标编号含无效字符") from None
    if not code or len(code) > 64:
        raise unavailable("指标编号不合法")
    effective = await resolve_user(session, ctx.preview_as) if ctx.preview_as else user
    if body.cr_id and effective.id != user.id:
        await metric_definition(session, code, user, ctx.ver, body.cr_id)
    return await evaluate_metric(session, effective, code, ver=ctx.ver, cr_id=body.cr_id)
