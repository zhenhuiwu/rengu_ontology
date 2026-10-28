"""指标 / 维度定义导出为 MetricFlow 语义层格式（只读）。

只导出当前空间里能被试算编译器完整解析的已发布指标：对象、属性、时间字段、默认过滤都经
compile_metric 校验，导出口径与试算一致；其余指标列入 skipped 并附原因，不猜测、不补默认值。
敏感 / 隐藏属性不导出为维度。行级权限不随定义导出，消费方必须自行按调用者范围过滤。
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import Date, DateTime, select
from sqlglot import exp

from app.common.auth import CurrentUser, check_func
from app.common.ontology import require_ontology_id
from app.core.errors import AppError, NotFound
from app.modules.static.metric_trial import (
    BoundFormula,
    compile_metric,
    execution_bindings,
    metric_definition,
    parse_formula,
    unavailable,
)
from app.modules.static.models import Dimension, Metric, ObjectType

AGG = {exp.Sum: "sum", exp.Count: "count", exp.Avg: "average", exp.Min: "min", exp.Max: "max"}
COMPARE = {exp.EQ: "=", exp.NEQ: "<>", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}
NOTES = [
    "仅含可试算的已发布指标；其余见 skipped。",
    "行级权限不随定义导出：消费方必须按调用者的数据范围过滤后再计算。",
]


def _literal(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal, float)):
        return str(value)
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    return "'" + str(value).replace("'", "''") + "'"


class _Model:
    """一个对象对应一个 semantic model；维度按引用逐步登记。"""

    def __init__(self, obj: ObjectType, gov):
        pk = next((p for p in obj.properties if p.api_name == obj.pk_property), None)
        self.obj, self.gov = obj, gov
        self.entity = obj.code
        self.out: dict[str, Any] = {
            "name": obj.code,
            "label": obj.display_name,
            "description": obj.description or "",
            "node_relation": {"schema_name": str(gov.table.schema or "public"), "alias": str(gov.table.name)},
            "defaults": {},
            "entities": [
                {
                    "name": obj.code,
                    "type": "primary",
                    "expr": (pk.backing_column or pk.source_column) if pk is not None else "id",
                }
            ],
            "dimensions": [],
            "measures": [],
        }

    def dimension(self, prop, col, label=None) -> str:
        if not any(d["name"] == prop.api_name for d in self.out["dimensions"]):
            row: dict[str, Any] = {
                "name": prop.api_name,
                "label": label or prop.display_name,
                "expr": str(col.key),
            }
            if isinstance(col.type, (Date, DateTime)):
                row.update(type="time", type_params={"time_granularity": "day"})
            else:
                row["type"] = "categorical"
            self.out["dimensions"].append(row)
        return prop.api_name


def _prop_of(obj: ObjectType, col):
    return next(p for p in obj.properties if (p.backing_column or p.source_column) == col.key)


def _filters(bound: BoundFormula, model: _Model, node) -> list[str]:
    from app.modules.static.explorer import scalar_filter

    if isinstance(node, exp.Paren):
        return _filters(bound, model, node.this)
    if isinstance(node, exp.And):
        return _filters(bound, model, node.this) + _filters(bound, model, node.expression)
    if type(node) not in COMPARE:
        raise unavailable("默认过滤尚不能确定执行含义；支持显式属性与标量比较")
    col = bound.column(node.this)
    value = node.expression
    if isinstance(value, (exp.Literal, exp.Boolean)):
        raw = value.this
    elif isinstance(value, exp.Column) and not value.table:
        raw = value.name
    else:
        raise unavailable("过滤值必须是明确的标量")
    if raw in ("是", "否") and col.type.python_type is bool:
        raw = raw == "是"
    name = model.dimension(_prop_of(bound.object, col), col)
    ref = (
        f"{{{{ TimeDimension('{model.entity}__{name}', 'day') }}}}"
        if isinstance(col.type, (Date, DateTime))
        else f"{{{{ Dimension('{model.entity}__{name}') }}}}"
    )
    return [f"{ref} {COMPARE[type(node)]} {_literal(scalar_filter(col, raw, 'formula'))}"]


def _export_metric(
    metric: dict, row: Metric, objects, ontology_id, models: dict, bindings=None, session=None
) -> list[dict]:
    bound, _expression, temporal, _time_field, _conds = compile_metric(
        metric, objects, ontology_id, bindings, session
    )
    if temporal is None:
        raise unavailable("语义层时间查询需要明确的时间字段")
    if getattr(bound.governed, "binding", None) is not None:
        raise unavailable(
            "该指标使用专用只读来源，可在指标页试算；当前语义层执行器尚不支持该来源",
            reason="SEMANTIC_SOURCE_UNSUPPORTED",
        )
    model = models.get(bound.object.code) or models.setdefault(
        bound.object.code, _Model(bound.object, bound.governed)
    )
    time_dim = model.dimension(_prop_of(bound.object, temporal), temporal)
    model.out["defaults"].setdefault("agg_time_dimension", time_dim)
    tree = parse_formula(metric.get("formula"))
    filters: list[str] = []
    if tree.args.get("where"):
        filters += _filters(bound, model, tree.args["where"].this)
    metadata = {r.get("k"): r.get("v") for r in metric.get("def", []) if isinstance(r, dict)}
    if metadata.get("默认过滤"):
        where = parse_formula("1 WHERE " + metadata["默认过滤"], normalize_separators=True)
        filters += _filters(bound, model, where.args["where"].this)
    root = tree.expressions[0]
    while isinstance(root, exp.Paren):
        root = root.this
    aggs = list(root.find_all(*AGG))
    meta = {"rengu": {"id": row.code, "version": row.version, "unit": row.unit, "status": row.status.value}}
    base = {"label": row.display_name, "description": metric.get("formula") or "", "config": {"meta": meta}}
    out, names = [], []
    single = len(aggs) == 1 and aggs[0] is root
    for i, node in enumerate(aggs):
        name = row.api_name if single else f"{row.api_name}__m{i + 1}"
        if isinstance(node, exp.Count) and isinstance(node.this, exp.Column) and not node.this.table:
            expr = model.out["entities"][0]["expr"]
        else:
            expr = str(bound.column(node.this).key)
        model.out["measures"].append(
            {"name": name, "agg": AGG[type(node)], "expr": expr, "agg_time_dimension": time_dim}
        )
        simple: dict[str, Any] = {"name": name, "type": "simple", "type_params": {"measure": name}}
        if filters:
            simple["filter"] = " AND ".join(filters)
        out.append(
            {**base, **simple}
            if single
            else {
                **simple,
                "label": f"{row.display_name}（第{i + 1}项）",
                "config": {"meta": {"rengu": {**meta["rengu"], "componentOf": row.api_name}}},
            }
        )
        names.append(name)
        if not single:
            node.replace(exp.column(name))
    if not single:
        out.append(
            {
                "name": row.api_name,
                **base,
                "type": "derived",
                "type_params": {
                    "expr": root.sql(dialect="postgres"),
                    "metrics": [{"name": n} for n in names],
                },
            }
        )
    return out


async def export_metricflow(session, user: CurrentUser, *, ver=None) -> dict:
    check_func(user, "metrics", "view")
    if ver:
        raise unavailable("历史快照没有保存完整执行映射，不能导出")
    ontology_id = require_ontology_id(session)
    objects = list(await session.scalars(select(ObjectType)))
    models: dict[str, _Model] = {}
    metrics, skipped = [], []
    for row in await session.scalars(select(Metric).order_by(Metric.code)):
        try:
            metric = await metric_definition(session, row.code, user)
            bindings = await execution_bindings(session, objects, metric)
            from app.common.sql_guard import ensure_governed_tables

            await ensure_governed_tables(session)
            metrics += _export_metric(metric, row, objects, ontology_id, models, bindings, session)
        except (AppError, NotFound) as error:
            issues = (error.details or {}).get("issues") or [
                {"reason": (error.details or {}).get("reason", error.code), "message": error.message}
            ]
            skipped.append({"id": row.code, "name": row.display_name, "issues": issues})
    # 已定义维度：来源对象已导出、来源属性已绑定且非敏感 / 隐藏时导出为维度。
    for dim in await session.scalars(select(Dimension).order_by(Dimension.code)):
        model = next((m for m in models.values() if m.obj.id == dim.object_id), None)
        prop = next((p for p in model.obj.properties if p.api_name == dim.attr), None) if model else None
        if prop is None or prop.is_sensitive or prop.visibility == "hidden" or prop.transform:
            continue
        column = prop.backing_column or prop.source_column
        binding = getattr(model.gov, "binding", None)
        if column in model.gov.table.c and (binding is None or prop.api_name in binding.out) and (getattr(model.gov, "projection", None) is None or column in model.gov.projection):
            model.dimension(prop, model.gov.table.c[column], label=dim.display_name)
    return {
        "semantic_models": [m.out for m in models.values()],
        "metrics": metrics,
        "skipped": skipped,
        "notes": NOTES,
    }
