"""MetricFlow 引擎适配：消费静态的 MetricFlow 导出（JSON 形状），编译成 SQL，经 sql_guard 执行。

链路（全部进程内，不走 HTTP）：
    export_metricflow(session, user)          # app/modules/static/metricflow_export.py（静态维护，不改）
      → compile_metrics(manifest, …)          # 本文件：MetricFlow 引擎或内置编译器 → 单条 SELECT
      → run_guarded(conn, sql, user)          # 行级过滤 + 本体边界，conn = await session.connection()

- 两个后端：
  - `metricflow`：可选依赖 `metricflow==0.213.*`（Apache-2.0，无 dbt-core；`uv sync --extra metricflow`）。
    导出 JSON → PydanticSemanticManifest（经官方 transformer 把 measure 写法转成 0.213 的
    metric_aggregation_params）→ MetricFlowEngine.explain（只渲染 SQL，不连库，SqlClient 是只渲染桩）。
    需要 time spine 配置（见 TIME_SPINE），普通 simple / derived 查询不会读它。
  - `builtin`：内置最小编译器（simple 指标，以及同一 semantic model 上 simple 组成的 derived 指标）。
  - `auto`（服务函数默认）/ `metricflow`：包没装时回退内置编译器，结果里 backend 字段写实际用的那个。
- 两个后端输出列名一致：维度用请求里的名字（`metric_time`、`<entity>__<dim>`），指标用指标名。
- 不做定义转换、不补口径：导出里没有的指标 / 维度一律 422，不猜测。
- 行级权限不随导出：编译结果只能经 run_guarded 执行（导出 notes 的要求）；带绑定参数的 SQL 直接拒绝。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from collections import OrderedDict
from dataclasses import dataclass
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
BACKENDS = ("auto", "builtin", "metricflow")
# MetricFlow 要求 project_configuration 里有 time spine（按天的日期表）。只有累计 / 补零 / 偏移类查询会读它，
# 读到时它也必须是登记过的受控表，否则 run_guarded 拒绝。导出自带 project_configuration 时以导出为准。
TIME_SPINE = {"schema_name": "public", "alias": "metricflow_time_spine", "column": "date_day"}
_FILTER_OPS = ("=", "<>", "!=", ">", ">=", "<", "<=")


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
    return compile_metrics(manifest, metrics, group_by, where, limit=limit, backend=backend).sql


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    backend: str  # 实际使用的后端：builtin / metricflow


def compile_metrics(
    manifest: dict[str, Any],
    metrics: list[str],
    group_by: list[str] = (),  # type: ignore[assignment]
    where: list[str] = (),  # type: ignore[assignment]
    *,
    limit: int | None = None,
    backend: str = "auto",
) -> CompiledQuery:
    """同 compile_query，另外返回实际使用的后端。auto / metricflow 在包未安装时回退 builtin。"""
    if backend not in BACKENDS:
        raise MetricQueryInvalid("未知引擎", details={"backend": backend, "allowed": list(BACKENDS)})
    if not metrics:
        raise MetricQueryInvalid("至少要一个指标")
    if limit is not None and (not isinstance(limit, int) or not 0 < limit <= MAX_LIMIT):
        raise MetricQueryInvalid("limit 超出范围", details={"limit": limit, "max": MAX_LIMIT})
    for text in where:
        if not isinstance(text, str) or not _USER_WHERE.match(text):
            raise MetricQueryInvalid("where 只支持「维度引用 比较符 标量」", details={"where": text})
    if backend != "builtin" and metricflow_available():
        return CompiledQuery(_compile_metricflow(manifest, metrics, group_by, where, limit), "metricflow")
    return CompiledQuery(_compile_builtin(manifest, metrics, group_by, where, limit), "builtin")


def _compile_builtin(
    manifest: dict[str, Any], metrics: list[str], group_by: list[str], where: list[str], limit: int | None
) -> str:
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
        query = query.limit(limit)
    return query.sql(dialect="postgres")


# ---- metricflow 后端（可选依赖） ----

_ENGINES: OrderedDict[str, Any] = OrderedDict()
_ENGINE_CACHE_SIZE = 8


def _mf_document(manifest: dict[str, Any]) -> dict[str, Any]:
    """导出 JSON → MetricFlow 语义清单文档：只取 semantic_models / metrics，补 time spine 配置。"""
    if not isinstance(manifest, dict):
        raise MetricQueryInvalid("导出格式不对：需要 JSON 对象")
    project = manifest.get("project_configuration") or {
        "time_spines": [
            {
                "node_relation": {"schema_name": TIME_SPINE["schema_name"], "alias": TIME_SPINE["alias"]},
                "primary_column": {"name": TIME_SPINE["column"], "time_granularity": "day"},
            }
        ]
    }
    return {
        "semantic_models": manifest.get("semantic_models") or [],
        "metrics": manifest.get("metrics") or [],
        "project_configuration": project,
    }


class _RenderOnlyClient:
    """满足 MetricFlow SqlClient 协议的只渲染桩：explain 只要方言和渲染器，不执行任何 SQL。"""

    def __init__(self) -> None:
        from metricflow.protocols.sql_client import SqlEngine
        from metricflow.sql.render.postgres import PostgresSQLSqlPlanRenderer

        self.sql_engine_type = SqlEngine.POSTGRES
        self.sql_plan_renderer = PostgresSQLSqlPlanRenderer()

    def render_bind_parameter_key(self, bind_parameter_key: str) -> str:
        return f"%({bind_parameter_key})s"

    def query(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("MetricFlow 只用于编译；执行必须走 run_guarded")

    execute = dry_run = query

    def close(self) -> None:
        return None


def _mf_engine(manifest: dict[str, Any]) -> Any:
    from metricflow.engine.metricflow_engine import MetricFlowEngine
    from metricflow_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest
    from metricflow_semantic_interfaces.transformations.semantic_manifest_transformer import (
        PydanticSemanticManifestTransformer,
    )
    from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

    doc = _mf_document(manifest)
    key = hashlib.sha256(
        json.dumps(doc, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
    if key in _ENGINES:
        _ENGINES.move_to_end(key)
        return _ENGINES[key]
    try:
        parsed = PydanticSemanticManifestTransformer.transform(PydanticSemanticManifest.parse_obj(doc))
        engine = MetricFlowEngine(SemanticManifestLookup(parsed), _RenderOnlyClient())
    except Exception as error:  # 清单不合 MetricFlow 规则：按查询无效处理，不回退猜测
        raise MetricQueryInvalid(
            "导出不能被 MetricFlow 解析", details={"error": str(error)[:500], "backend": "metricflow"}
        ) from error
    _ENGINES[key] = engine
    while len(_ENGINES) > _ENGINE_CACHE_SIZE:
        _ENGINES.popitem(last=False)
    return engine


def _mf_group_by(man: _Manifest, ref: str) -> str:
    """请求里的维度名 → MetricFlow 名：metric_time / 时间维度带 __day 粒度，类别维度原样。"""
    if ref == METRIC_TIME:
        return f"{METRIC_TIME}__day"
    if not isinstance(ref, str) or not _NAME.match(ref):
        raise MetricQueryInvalid("名称不合法", details={"name": ref})
    entity = ref.partition("__")[0]
    model = man.models.get(entity)
    if model is None:
        raise MetricQueryInvalid("维度不存在", details={"dimension": ref})
    _, is_time = man.dimension(model, ref)
    return f"{ref}__day" if is_time else ref


def _compile_metricflow(
    manifest: dict[str, Any], metrics: list[str], group_by: list[str], where: list[str], limit: int | None
) -> str:
    from metricflow.engine.metricflow_engine import MetricFlowQueryRequest

    man = _Manifest(manifest)
    names = list(dict.fromkeys(metrics))
    for name in names:
        man.metric(name)  # 导出里没有的指标先按 422 拒绝（含 skipped 提示）
    dims = list(dict.fromkeys(group_by))
    mf_dims = [_mf_group_by(man, ref) for ref in dims]
    engine = _mf_engine(manifest)
    request = MetricFlowQueryRequest.create(
        metric_names=names,
        group_by_names=mf_dims,
        where_constraints=list(where) or None,
        order_by_names=mf_dims or None,
        limit=limit,
    )
    try:
        statement = engine.explain(request).sql_statement
    except Exception as error:
        raise MetricQueryInvalid(
            "MetricFlow 不能编译这个查询", details={"error": str(error)[:500], "backend": "metricflow"}
        ) from error
    if statement.bind_parameter_set.param_items:
        raise MetricQueryInvalid("MetricFlow 生成了绑定参数，网关不接受", details={"backend": "metricflow"})
    # 外层统一列名（与内置编译器一致），排序 / limit 已在内层。
    inner = sqlglot.parse_one(statement.sql, read="postgres")
    outer = exp.select(
        *[exp.alias_(exp.column(_ident(m)), _ident(r)) for m, r in zip(mf_dims, dims, strict=True)],
        *[exp.column(_ident(n)) for n in names],
    ).from_(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier("mf"))))
    if dims:
        outer = outer.order_by(*[exp.Literal.number(i + 1) for i in range(len(dims))])
    return outer.sql(dialect="postgres")


# ---- 服务函数（智能问数） ----


def filter_text(item: str | dict[str, Any]) -> str:
    """过滤条件：模板字符串原样；结构化 {"dimension","op","value"} 转成 Dimension 模板（值做字面量转义）。"""
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        raise MetricQueryInvalid("过滤条件格式不对", details={"filter": item})
    dim, op, value = item.get("dimension"), item.get("op", "="), item.get("value")
    if not isinstance(dim, str) or not _NAME.match(dim) or op not in _FILTER_OPS:
        raise MetricQueryInvalid("过滤条件格式不对", details={"filter": item})
    if isinstance(value, bool):
        literal = "TRUE" if value else "FALSE"
    elif isinstance(value, int | float):
        literal = repr(value)
    elif isinstance(value, str):
        literal = "'" + value.replace("'", "''") + "'"
    else:
        raise MetricQueryInvalid("过滤值只支持字符串 / 数值 / 布尔", details={"filter": item})
    return f"{{{{ Dimension('{dim}') }}}} {op} {literal}"


async def query_metrics(
    session: Any,
    user: Any,
    metrics: list[str],
    group_by: list[str] = (),  # type: ignore[assignment]
    where: list[str] = (),  # type: ignore[assignment]
    *,
    limit: int | None = 1000,
    backend: str = "builtin",
) -> dict[str, Any]:
    """进程内：导出（含 metrics view 权限校验）→ 编译 → run_guarded（行级过滤 + 本体边界）。"""
    from app.common.sql_guard import run_guarded
    from app.modules.static.metricflow_export import export_metricflow

    manifest = await export_metricflow(session, user)
    compiled = compile_metrics(manifest, metrics, group_by, where, limit=limit, backend=backend)
    result = await run_guarded(await session.connection(), compiled.sql, user)
    columns = list(result.keys())
    return {
        "sql": compiled.sql,
        "backend": compiled.backend,
        "columns": columns,
        "rows": [list(r) for r in result.fetchall()],
    }


async def metric_query(
    session: Any,
    user: Any,
    *,
    metrics: list[str],
    dimensions: list[str] = (),  # type: ignore[assignment]
    filters: list[str | dict[str, Any]] = (),  # type: ignore[assignment]
    limit: int | None = 1000,
    backend: str = "auto",
) -> dict[str, Any]:
    """智能问数用：指标 + 维度 + 过滤 → {sql, backend, columns, rows}。

    - user 是调用方 CurrentUser（会话已绑定本体范围）；导出按其 metrics view 权限，执行按其行级范围。
    - dimensions：`<entity>__<dim>` 或 `metric_time`（默认时间维度，按天）。
    - filters：`{{ Dimension('<entity>__<dim>') }} <op> <标量>` 或 {"dimension", "op", "value"}，多条 AND。
    - backend：auto（默认，MetricFlow 已安装则用它，否则内置）/ metricflow（同 auto）/ builtin。
    - 返回的 sql 是编译结果（未注入行级条件）；rows 是经 run_guarded 执行后的结果。
    """
    return await query_metrics(
        session,
        user,
        list(metrics),
        list(dimensions),
        [filter_text(f) for f in filters],
        limit=limit,
        backend=backend,
    )
