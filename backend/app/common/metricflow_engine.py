"""MetricFlow 引擎适配骨架：消费静态的 MetricFlow 导出（JSON 形状），编译成 SQL，经 sql_guard 执行。

链路（全部进程内，不走 HTTP）：
    export_metricflow(session, user)          # app/modules/static/metricflow_export.py（静态维护，不改）
      → compile_query(manifest, metrics, …)   # 本文件：编译成单条 SELECT
      → run_guarded(conn, sql, user)          # 行级过滤 + 本体边界，conn = await session.connection()

- 依赖可选：`metricflow` 库没装时用内置的最小编译器（只支持 simple 指标，以及由同一 semantic model 上
  simple 指标组成的 derived 指标）；装了也暂不接管（`backend="metricflow"` 明确报未接入），
  不改依赖锁。只用 JSON 形状，不依赖 PyYAML。
- 不做定义转换、不补口径：导出里没有的指标 / 维度一律 422，不猜测。
- 行级权限不随导出：编译结果只能经 run_guarded 执行（导出 notes 的要求）。
"""

from __future__ import annotations

import importlib.util
import re
from typing import Any

import sqlglot
from sqlglot import exp

from app.core.errors import AppError

AGG_SQL = {"sum": "SUM", "count": "COUNT", "average": "AVG", "min": "MIN", "max": "MAX"}
METRIC_TIME = "metric_time"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REF = re.compile(
    r"\{\{\s*(?:Dimension\(\s*'(?P<d>[A-Za-z0-9_]+)'\s*\)"
    r"|TimeDimension\(\s*'(?P<t>[A-Za-z0-9_]+)'\s*,\s*'day'\s*\))\s*\}\}"
)
_USER_WHERE = re.compile(
    r"^\s*(?P<ref>\{\{[^}]*\}\})\s*(?P<op>=|<>|!=|>=|<=|>|<)\s*"
    r"(?P<val>'(?:[^']|'')*'|-?\d+(?:\.\d+)?|TRUE|FALSE)\s*$",
    re.IGNORECASE,
)
MAX_LIMIT = 10000


class MetricQueryInvalid(AppError):
    status_code, code, message = 422, "METRIC_QUERY_INVALID", "指标查询无效"


def metricflow_available() -> bool:
    """可选依赖探测：只看能否导入，不导入（避免拉起其依赖）。"""
    return importlib.util.find_spec("metricflow") is not None


def _ident(name: str) -> exp.Identifier:
    if not isinstance(name, str) or not _NAME.match(name):
        raise MetricQueryInvalid("名称不合法", details={"name": name})
    return exp.to_identifier(name, quoted=True)


class _Manifest:
    def __init__(self, manifest: dict[str, Any]):
        if not isinstance(manifest, dict):
            raise MetricQueryInvalid("导出格式不对：需要 JSON 对象")
        self.models = {m["name"]: m for m in manifest.get("semantic_models") or []}
        self.metrics = {m["name"]: m for m in manifest.get("metrics") or []}
        self.measures = {
            meas["name"]: (model, meas)
            for model in self.models.values()
            for meas in model.get("measures") or []
        }

    def metric(self, name: str) -> dict[str, Any]:
        if name not in self.metrics:
            raise MetricQueryInvalid(
                "导出里没有这个指标（可能未达试算条件，见 skipped）", details={"metric": name}
            )
        return self.metrics[name]

    def simple(self, name: str) -> tuple[dict, dict, str | None]:
        metric = self.metric(name)
        if metric.get("type") != "simple":
            raise MetricQueryInvalid("内置编译器只支持 simple / derived 指标", details={"metric": name})
        measure = (metric.get("type_params") or {}).get("measure")
        if measure not in self.measures:
            raise MetricQueryInvalid("指标引用的度量不存在", details={"metric": name, "measure": measure})
        model, meas = self.measures[measure]
        return model, meas, metric.get("filter")

    def dimension(self, model: dict, ref: str) -> tuple[dict, bool]:
        entity, _, dim = ref.partition("__")
        if entity != model["name"]:
            raise MetricQueryInvalid("内置编译器不支持跨 semantic model 维度", details={"dimension": ref})
        for d in model.get("dimensions") or []:
            if d["name"] == dim:
                return d, d.get("type") == "time"
        raise MetricQueryInvalid("维度不存在", details={"dimension": ref})


def _column(dim: dict) -> exp.Expression:
    return exp.column(_ident(dim["expr"]))


def _filter_sql(man: _Manifest, model: dict, text: str) -> exp.Expression:
    """导出里的过滤模板 → 条件：模板引用换成列，其余部分交给 sqlglot 解析（只允许比较 / AND）。"""

    def repl(match: re.Match) -> str:
        dim, _ = man.dimension(model, match.group("d") or match.group("t"))
        return _column(dim).sql(dialect="postgres")

    sql = _REF.sub(repl, text)
    if "{{" in sql:
        raise MetricQueryInvalid("过滤模板只支持 Dimension / TimeDimension(day)", details={"filter": text})
    cond = sqlglot.parse_one(sql, read="postgres")
    allowed = (exp.And, exp.Paren, exp.Column, exp.Identifier, exp.Literal, exp.Boolean, exp.Neg, exp.Null)
    allowed += (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
    if any(not isinstance(node, allowed) for node in cond.walk()):
        raise MetricQueryInvalid("过滤条件只支持列与标量的比较（AND 连接）", details={"filter": text})
    return cond


def _measure_sql(meas: dict, cond: exp.Expression | None) -> exp.Expression:
    agg = meas.get("agg")
    if agg not in AGG_SQL:
        raise MetricQueryInvalid("不支持的聚合", details={"measure": meas.get("name"), "agg": agg})
    value: exp.Expression = exp.column(_ident(meas["expr"]))
    if cond is not None:
        value = exp.Case(ifs=[exp.If(this=cond, true=value)])
    return exp.Anonymous(this=AGG_SQL[agg], expressions=[value])


def compile_query(
    manifest: dict[str, Any],
    metrics: list[str],
    group_by: list[str] = (),  # type: ignore[assignment]
    where: list[str] = (),  # type: ignore[assignment]
    *,
    limit: int | None = None,
    backend: str = "builtin",
) -> str:
    """导出 JSON + 指标 / 维度名 → 单条 SELECT（未注入行级条件；只能交给 run_guarded 执行）。

    group_by：`<entity>__<dimension>` 或 `metric_time`（= 模型默认时间维度，按天）。
    where：`{{ Dimension('<entity>__<dim>') }} <op> <标量>`，多条 AND。
    """
    if backend == "metricflow":
        raise MetricQueryInvalid(
            "MetricFlow 引擎尚未接入（可选依赖）", details={"installed": metricflow_available()}
        )
    if backend != "builtin":
        raise MetricQueryInvalid("未知引擎", details={"backend": backend})
    if not metrics:
        raise MetricQueryInvalid("至少要一个指标")
    man = _Manifest(manifest)
    selects: list[exp.Expression] = []
    models: set[str] = set()
    model: dict | None = None

    def component(name: str) -> exp.Expression:
        nonlocal model
        m, meas, ftext = man.simple(name)
        models.add(m["name"])
        model = m
        return _measure_sql(meas, _filter_sql(man, m, ftext) if ftext else None)

    for name in dict.fromkeys(metrics):
        metric = man.metric(name)
        if metric.get("type") == "derived":
            params = metric.get("type_params") or {}
            parts = {c["name"]: component(c["name"]) for c in params.get("metrics") or []}
            tree = sqlglot.parse_one(params.get("expr") or "", read="postgres")
            for col in list(tree.find_all(exp.Column)):
                if col.name not in parts:
                    raise MetricQueryInvalid(
                        "派生指标引用了未声明的分项", details={"metric": name, "ref": col.name}
                    )
                col.replace(exp.Paren(this=parts[col.name].copy()))
            if any(isinstance(n, (exp.Select, exp.Subquery)) for n in tree.walk()):
                raise MetricQueryInvalid("派生表达式不合法", details={"metric": name})
            selects.append(exp.alias_(tree, _ident(name)))
        else:
            selects.append(exp.alias_(component(name), _ident(name)))
    if len(models) != 1 or model is None:
        raise MetricQueryInvalid(
            "内置编译器只支持同一 semantic model 上的指标", details={"models": sorted(models)}
        )
    dims: list[exp.Expression] = []
    for ref in dict.fromkeys(group_by):
        if ref == METRIC_TIME:
            tname = (model.get("defaults") or {}).get("agg_time_dimension")
            if not tname:
                raise MetricQueryInvalid("模型没有默认时间维度", details={"model": model["name"]})
            d, _ = man.dimension(model, f"{model['name']}__{tname}")
            dims.append(exp.alias_(exp.cast(_column(d), "date"), _ident(METRIC_TIME)))
        else:
            d, is_time = man.dimension(model, ref)
            col = exp.cast(_column(d), "date") if is_time else _column(d)
            dims.append(exp.alias_(col, _ident(ref)))
    conds = []
    for text in where:
        if not isinstance(text, str) or not _USER_WHERE.match(text):
            raise MetricQueryInvalid("where 只支持「维度引用 比较符 标量」", details={"where": text})
        conds.append(_filter_sql(man, model, text))
    rel = model.get("node_relation") or {}
    table = exp.table_(_ident(rel.get("alias", "")), db=_ident(rel.get("schema_name") or "public"))
    query = exp.select(*dims, *selects).from_(table)
    for cond in conds:
        query = query.where(cond)
    if dims:
        query = query.group_by(*[exp.Literal.number(i + 1) for i in range(len(dims))])
        query = query.order_by(*[exp.Literal.number(i + 1) for i in range(len(dims))])
    if limit is not None:
        if not isinstance(limit, int) or not 0 < limit <= MAX_LIMIT:
            raise MetricQueryInvalid("limit 超出范围", details={"limit": limit, "max": MAX_LIMIT})
        query = query.limit(limit)
    return query.sql(dialect="postgres")


async def query_metrics(
    session: Any,
    user: Any,
    metrics: list[str],
    group_by: list[str] = (),  # type: ignore[assignment]
    where: list[str] = (),  # type: ignore[assignment]
    *,
    limit: int | None = 1000,
) -> dict[str, Any]:
    """进程内：导出（含 metrics view 权限校验）→ 编译 → run_guarded（行级过滤 + 本体边界）。"""
    from app.common.sql_guard import run_guarded
    from app.modules.static.metricflow_export import export_metricflow

    manifest = await export_metricflow(session, user)
    sql = compile_query(manifest, metrics, group_by, where, limit=limit)
    result = await run_guarded(await session.connection(), sql, user)
    columns = list(result.keys())
    return {"sql": sql, "columns": columns, "rows": [list(r) for r in result.fetchall()]}
