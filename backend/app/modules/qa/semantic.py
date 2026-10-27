"""问数接指标层（MetricFlow 导出 + 小白的内置编译器 `app.common.metricflow_engine`）。

题目问的是**可试算的已发布指标**（static `GET /exports/metricflow` 导出的那批）时，模型不写 SQL，
只输出 ```json {"metric": {...}} ```（指标 / 维度 / 过滤 / 时间），这里换成编译器参数，SQL 由编译器生成，
仍交给 sql_guard（`assert_single_select` + `run_guarded`，行级条件照常注入）执行。
编不出来（MetricQueryInvalid、不支持的时间粒度等）→ 回喂一次，退回原来的 SQL 路径。

不改编译器；它缺的能力见 handoff/qa.md（月 / 周粒度、跨模型维度、IN / LIKE 过滤等）。
没有任何业务硬编码：指标、维度、时间维度全部来自导出 JSON。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.common.metricflow_engine import METRIC_TIME, MetricQueryInvalid

OPS = ("=", "<>", "!=", ">=", "<=", ">", "<")
GRAINS = ("day", "none")
MAX_METRICS = 40  # prompt 里最多列多少个指标
TIME_LABEL = "日期"
CALIBER = "指标层（MetricFlow 导出 + 内置编译器生成 SQL）"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")

METRIC_GUIDE = (
    "\n5. 问题问的指标在“指标层”里（同一个模型）时，不写 SQL，只输出 "
    '```json {"metric": {"metrics": ["指标名"], "dimensions": ["模型__维度"], '
    '"filters": [{"dimension": "模型__维度", "op": "=", "value": "库里的取值"}], '
    '"time": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "grain": "day 或 none"}}} ```；'
    "名字只能照抄“指标层”里的；end 不含当天（左闭右开）；grain=day 表示按天看趋势，"
    "none 表示整段汇总；不问时间就不写 time。要按月 / 周、跨模型维度、IN / LIKE / 排序取前几名等"
    "指标层表达不了的，照常写 SQL。\n"
)


class MetricPlanInvalid(MetricQueryInvalid):
    """问数这边就能判定编不出的（粒度不支持、时间格式不对）：同 MetricQueryInvalid，走回喂。"""


@dataclass
class MetricPlan:
    metrics: list[str]
    group_by: list[str] = field(default_factory=list)
    where: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)  # 输出列名 → 中文标题
    echo: dict[str, Any] = field(default_factory=dict)  # 回答里展示的指标查询（不含 SQL）


async def metric_layer(session, user) -> dict[str, Any] | None:
    """当前工作空间的指标层导出；没有指标查看权限 / 导出失败 / 没有可试算指标 → None（只走 SQL）。"""
    from app.modules.static.metricflow_export import export_metricflow

    try:
        async with session.begin_nested():  # savepoint：导出失败不弄坏请求事务
            manifest = await export_metricflow(session, user)
    except Exception:
        return None
    return manifest if top_metrics(manifest) else None


def _component(m: dict[str, Any]) -> bool:
    return bool((((m.get("config") or {}).get("meta") or {}).get("rengu") or {}).get("componentOf"))


def top_metrics(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    """对外的指标（派生指标的分项 `x__m1` 不列）。"""
    return [m for m in (manifest or {}).get("metrics") or [] if not _component(m)]


def _measure_model(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        meas["name"]: model["name"]
        for model in manifest.get("semantic_models") or []
        for meas in model.get("measures") or []
    }


def metric_model(manifest: dict[str, Any], name: str) -> str | None:
    """指标所在的 semantic model（派生指标取第一个分项的）。"""
    metrics = {m["name"]: m for m in manifest.get("metrics") or []}
    by_measure = _measure_model(manifest)
    m = metrics.get(name)
    if m is None:
        return None
    params = m.get("type_params") or {}
    if m.get("type") == "derived":
        parts = [c.get("name") for c in params.get("metrics") or []]
        return next((metric_model(manifest, p) for p in parts if p), None)
    return by_measure.get(params.get("measure"))


def semantic_lines(manifest: dict[str, Any] | None) -> list[str]:
    """prompt 的“指标层”：按模型列指标（名、中文名、单位）、可分组 / 过滤的维度和默认时间维度。"""
    if not manifest:
        return []
    by_model: dict[str, list[dict[str, Any]]] = {}
    for m in top_metrics(manifest)[:MAX_METRICS]:
        if model := metric_model(manifest, m["name"]):
            by_model.setdefault(model, []).append(m)
    out = []
    for model in manifest.get("semantic_models") or []:
        ms = by_model.get(model["name"])
        if not ms:
            continue
        time_dim = (model.get("defaults") or {}).get("agg_time_dimension")
        out.append(f"### 模型 {model['name']}（{model.get('label') or model['name']}）")
        for m in ms:
            unit = ((m.get("config") or {}).get("meta") or {}).get("rengu", {}).get("unit")
            out.append(f"- 指标 {m['name']}：{m.get('label') or m['name']}" + (f"（{unit}）" if unit else ""))
        dims = [
            f"{model['name']}__{d['name']}（{d.get('label') or d['name']}"
            + ("，时间" if d.get("type") == "time" else "")
            + "）"
            for d in model.get("dimensions") or []
        ]
        out.append("- 维度：" + ("、".join(dims) if dims else "无"))
        if time_dim:
            out.append(f"- 时间：time 按 {model['name']}__{time_dim} 过滤；grain=day 时输出列 {METRIC_TIME}")
    return out


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    raise MetricPlanInvalid("过滤值只能是字符串 / 数字 / 布尔", details={"value": str(value)[:50]})


def _time_value(raw: Any) -> str:
    text = str(raw or "").strip()
    if not _DATE.match(text):
        raise MetricPlanInvalid("时间要写成 YYYY-MM-DD", details={"time": text[:40]})
    return text.replace("T", " ")


def _dimension_index(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        f"{model['name']}__{d['name']}": d
        for model in manifest.get("semantic_models") or []
        for d in model.get("dimensions") or []
    }


def plan(obj: dict[str, Any], manifest: dict[str, Any], now: datetime) -> MetricPlan:
    """模型的 {"metric": {...}} → 编译器参数。只做结构校验，名字对不对交给编译器（报 MetricQueryInvalid）。

    时间上界不超过 now（未来的数据不存在，跟 SQL 路径的 cap_time_upper 一致）。
    """
    body = obj.get("metric") if isinstance(obj.get("metric"), dict) else {}
    metrics = [str(m) for m in body.get("metrics") or [] if str(m).strip()]
    if not metrics:
        raise MetricPlanInvalid("至少要一个指标")
    labels = {m["name"]: m.get("label") or m["name"] for m in manifest.get("metrics") or []}
    dims = _dimension_index(manifest)
    group_by = [str(d) for d in body.get("dimensions") or [] if str(d).strip()]
    time = body.get("time") if isinstance(body.get("time"), dict) else {}
    grain = str(time.get("grain") or "none").lower()
    if grain not in GRAINS:
        raise MetricPlanInvalid("指标层只支持按天（day）或整段汇总（none）", details={"grain": grain})
    if grain == "day" and METRIC_TIME not in group_by:
        group_by.append(METRIC_TIME)
    where = []
    for f in body.get("filters") or []:
        if not isinstance(f, dict):
            raise MetricPlanInvalid("filters 每项要是 {dimension, op, value}")
        ref, op = str(f.get("dimension") or ""), str(f.get("op") or "=").strip()
        if op not in OPS:
            raise MetricPlanInvalid("过滤只支持比较符 = <> >= <= > <", details={"op": op})
        d = dims.get(ref)
        tag = (
            f"{{{{ TimeDimension('{ref}', 'day') }}}}"
            if d is not None and d.get("type") == "time"
            else f"{{{{ Dimension('{ref}') }}}}"
        )
        where.append(f"{tag} {op} {_scalar(f.get('value'))}")
    window: dict[str, str] = {}
    if time.get("start") or time.get("end"):
        model_name = metric_model(manifest, metrics[0])
        model = next((m for m in manifest.get("semantic_models") or [] if m["name"] == model_name), None)
        tname = ((model or {}).get("defaults") or {}).get("agg_time_dimension")
        if not tname:
            raise MetricPlanInvalid("该指标没有默认时间维度，不能按时间过滤", details={"metric": metrics[0]})
        ref = f"{{{{ TimeDimension('{model_name}__{tname}', 'day') }}}}"
        if time.get("start"):
            window["start"] = _time_value(time["start"])
            where.append(f"{ref} >= '{window['start']}'")
        cap = now.strftime("%Y-%m-%d %H:%M:%S")
        end = _time_value(time["end"]) if time.get("end") else cap
        end = min(end if len(end) > 10 else end + " 00:00:00", cap)
        window["end"] = end
        where.append(f"{ref} < '{end}'")
    out_labels = {m: labels.get(m, m) for m in metrics}
    out_labels |= {g: (dims.get(g) or {}).get("label") or g for g in group_by if g != METRIC_TIME}
    out_labels[METRIC_TIME] = TIME_LABEL
    echo = {
        "metrics": [{"name": m, "label": out_labels[m]} for m in metrics],
        "dimensions": [{"name": g, "label": out_labels[g]} for g in group_by],
        "filters": [
            {"dimension": str(f.get("dimension")), "op": str(f.get("op") or "="), "value": f.get("value")}
            for f in body.get("filters") or []
            if isinstance(f, dict)
        ],
        "time": {**window, "grain": grain} if window or grain == "day" else None,
    }
    return MetricPlan(metrics=metrics, group_by=group_by, where=where, labels=out_labels, echo=echo)


def compile_plan(manifest: dict[str, Any], p: MetricPlan) -> str:
    from app.common.metricflow_engine import compile_query

    return compile_query(manifest, p.metrics, p.group_by, p.where)


def fallback_message(error: MetricQueryInvalid) -> str:
    details = "；".join(f"{k}={v}" for k, v in (error.details or {}).items())
    return (
        f"上面的指标查询指标层编译不了：{error.message}"
        + (f"（{details}）" if details else "")
        + "。请改为直接输出一条 SQL。"
    )
