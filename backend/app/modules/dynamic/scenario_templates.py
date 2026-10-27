"""情景推演模板（元数据驱动）：目标对象类型、范围筛选、数值驱动、输出指标、影响明细列、建议动作。

- 模板定义：按本体存在 scenario_template 表（dynamic_0024；RLS 只看到当前空间），definition 为单条模板；
  ontology 1 的初始配置由迁移从 seeds/json/dynamic/SC_TEMPLATES.json 导入
  （与 src/mock/scenarioTemplates.ts 同一份）。
  其他空间没有模板时返回空列表，template_of 抛 503。
- 引擎按模板 engine 键插拔（ENGINES）；演示模板 demo_retail = rules_sim.simulate / stage_plan（原样）。
- 本模块只做通用校验：范围 / 驱动按模板字段校验取值，未知键、越界、不在可选值里一律 422。
- 范围项可声明 propRef="对象类型.属性"：且引擎 accepts_facet_values 时，有数据（facets hasData）
  可选值 = allOption + 分面取值，否则只用模板固定 options（与前端一致）。
"""

import math
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import require_ontology_id
from app.core.errors import AppError
from app.modules.dynamic.rules_models import ScenarioTemplate
from app.modules.dynamic.rules_sim import simulate, stage_plan

DEFAULT_TEMPLATE = "demo_retail"

ENGINES: dict[str, dict[str, Callable[..., Any]]] = {
    # accepts_facet_values：引擎能否按分面原值计算；False 时范围只用模板固定 options（演示引擎只认固定项）
    "demo_retail": {"simulate": simulate, "stage_plan": stage_plan, "accepts_facet_values": False},
}


async def templates(session: AsyncSession) -> list[dict[str, Any]]:
    """当前空间的模板定义（按 sort, id）；返回副本，调用方可以就地改。"""
    require_ontology_id(session)
    q = select(ScenarioTemplate).order_by(ScenarioTemplate.sort, ScenarioTemplate.id)
    rows = await session.scalars(q)
    return [deepcopy(r.definition) for r in rows]


async def template_of(session: AsyncSession, code: str | None) -> dict[str, Any]:
    ts = await templates(session)
    if not ts:
        raise AppError(
            "当前本体尚未配置推演模板与执行器", code="SCENARIO_EXECUTOR_UNAVAILABLE", status_code=503
        )
    return next((t for t in ts if t["code"] == code), ts[0])


async def engine_of(session: AsyncSession, code: str | None) -> dict[str, Callable[..., Any]]:
    return ENGINES[(await template_of(session, code))["engine"]]


def driver_fields(t: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for g in t["driverGroups"] for r in g["rows"] for f in r["fields"]]


def defaults(fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {f["key"]: f["default"] for f in fields}


def no_driver(t: dict[str, Any], d: dict[str, Any]) -> bool:
    return all(
        d[f["key"]] == f["default"] or any(d.get(k) == 0 for k in f.get("effectNeeds", []))
        for f in driver_fields(t)
    )


async def scope_options(session: Any, user: Any, t: dict[str, Any]) -> dict[str, list[Any]]:
    """范围项按 propRef 取 static 分面取值；分面无数据 / 不可筛选时不覆盖（沿用模板 options）。"""
    from app.modules.static.explorer import ExplorerService

    if not ENGINES[t["engine"]].get("accepts_facet_values"):
        return {}
    refs = [(f, *f["propRef"].split(".", 1)) for f in t["scope"] if f.get("propRef")]
    out: dict[str, list[Any]] = {}
    cache_: dict[str, list[dict[str, Any]]] = {}
    for f, ot, prop in refs:
        if ot not in cache_:
            cache_[ot] = await ExplorerService(session).facets(ot, {}, user)
        fc = next((x for x in cache_[ot] if x["key"] == prop), None)
        if fc and fc.get("hasData") and fc["kind"] == "enum" and fc["values"]:
            head = [f["allOption"]] if f.get("allOption") is not None else []
            out[f["key"]] = head + [v["value"] for v in fc["values"] if v["value"] not in head]
    return out


def _is_int(f: dict[str, Any]) -> bool:
    """整数型数值项：默认值与步长（有的话）都是整数（演示模板的周数 / 价格 / 天数等全是）。"""
    step = f.get("step")
    return isinstance(f["default"], int) and (step is None or float(step).is_integer())


def _check(
    prefix: str,
    fields: list[dict[str, Any]],
    raw: dict[str, Any],
    errs: list[tuple[str, str]],
    opts: dict[str, list[Any]] | None = None,
):
    out: dict[str, Any] = {}
    known = {f["key"] for f in fields}
    errs += [(f"{prefix}.{k}", "模板里没有这一项") for k in raw if k not in known]
    for f in fields:
        k, v = f["key"], raw.get(f["key"], f["default"])
        loc = f"{prefix}.{k}"
        num = isinstance(f["default"], (int, float))
        if num and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            errs.append((loc, f"{f['label']}必须是数字"))
            continue
        if num and _is_int(f) and v != int(v):
            errs.append((loc, f"{f['label']}必须是整数"))
            continue
        if num and _is_int(f):
            v = int(v)
        if not num and not isinstance(v, str):
            errs.append((loc, f"{f['label']}必须是文本"))
            continue
        if f["kind"] == "select" and v not in (opts or {}).get(k, f.get("options", [])):
            errs.append((loc, f.get("invalidMsg") or f"{f['label']}不在可选列表里"))
            continue
        lo, hi = f.get("min"), f.get("max")
        if num and ((lo is not None and v < lo) or (hi is not None and v > hi)):
            errs.append((loc, f"{f['label']}应在 {lo} ~ {hi}"))
            continue
        out[k] = v
    return out


def validate(
    t: dict[str, Any],
    scope: dict[str, Any],
    drivers: dict[str, Any],
    scope_opts: dict[str, list[Any]] | None = None,
):
    """返回 (scope, drivers)；键顺序按模板字段；when 不满足的驱动置 offValue；scope_opts 覆盖范围可选值。"""
    errs: list[tuple[str, str]] = []
    s = _check("scope", t["scope"], scope, errs, scope_opts)
    d = _check("drivers", driver_fields(t), drivers, errs)
    if not errs:
        for f in driver_fields(t):
            w = f.get("when")
            if w and d[w["key"]] == w["not"]:
                d[f["key"]] = f.get("offValue", f["default"])
    return s, d, errs
