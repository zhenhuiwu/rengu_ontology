"""情景推演模板（元数据驱动）：目标对象类型、范围筛选、数值驱动、输出指标、影响明细列、建议动作。

- 模板定义：seeds/json/dynamic/SC_TEMPLATES.json（与 src/mock/scenarioTemplates.ts 同一份），
  EXTENSION POINT (PDE): 改成 scenario_template 表 / 本体配置后从库里读。
- 引擎按模板 engine 键插拔（ENGINES）；演示模板 demo_retail = rules_sim.simulate / stage_plan（原样）。
- 本模块只做通用校验：范围 / 驱动按模板字段校验取值，未知键、越界、不在可选值里一律 422。
"""

import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

from app.modules.dynamic.rules_sim import simulate, stage_plan

_JSON = Path(__file__).resolve().parents[3] / "seeds" / "json" / "dynamic" / "SC_TEMPLATES.json"
DEFAULT_TEMPLATE = "demo_retail"

ENGINES: dict[str, dict[str, Callable[..., Any]]] = {
    "demo_retail": {"simulate": simulate, "stage_plan": stage_plan},
}


@cache
def templates() -> list[dict[str, Any]]:
    return json.loads(_JSON.read_text(encoding="utf-8"))


def template_of(code: str | None) -> dict[str, Any]:
    ts = templates()
    return next((t for t in ts if t["code"] == code), ts[0])


def engine_of(code: str | None) -> dict[str, Callable[..., Any]]:
    return ENGINES[template_of(code)["engine"]]


def driver_fields(t: dict[str, Any]) -> list[dict[str, Any]]:
    return [f for g in t["driverGroups"] for r in g["rows"] for f in r["fields"]]


def defaults(fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {f["key"]: f["default"] for f in fields}


def no_driver(t: dict[str, Any], d: dict[str, Any]) -> bool:
    return all(
        d[f["key"]] == f["default"] or any(d.get(k) == 0 for k in f.get("effectNeeds", []))
        for f in driver_fields(t)
    )


def _check(prefix: str, fields: list[dict[str, Any]], raw: dict[str, Any], errs: list[tuple[str, str]]):
    out: dict[str, Any] = {}
    known = {f["key"] for f in fields}
    errs += [(f"{prefix}.{k}", "模板里没有这一项") for k in raw if k not in known]
    for f in fields:
        k, v = f["key"], raw.get(f["key"], f["default"])
        loc = f"{prefix}.{k}"
        num = isinstance(f["default"], (int, float))
        if num and (isinstance(v, bool) or not isinstance(v, (int, float))):
            errs.append((loc, f"{f['label']}必须是数字"))
            continue
        if not num and not isinstance(v, str):
            errs.append((loc, f"{f['label']}必须是文本"))
            continue
        if f["kind"] == "select" and v not in f.get("options", []):
            errs.append((loc, f.get("invalidMsg") or f"{f['label']}不在可选列表里"))
            continue
        lo, hi = f.get("min"), f.get("max")
        if num and ((lo is not None and v < lo) or (hi is not None and v > hi)):
            errs.append((loc, f"{f['label']}应在 {lo} ~ {hi}"))
            continue
        out[k] = v
    return out


def validate(t: dict[str, Any], scope: dict[str, Any], drivers: dict[str, Any]):
    """返回 (scope, drivers)；键顺序按模板字段顺序；when 不满足的驱动置 offValue。"""
    errs: list[tuple[str, str]] = []
    s = _check("scope", t["scope"], scope, errs)
    d = _check("drivers", driver_fields(t), drivers, errs)
    if not errs:
        for f in driver_fields(t):
            w = f.get("when")
            if w and d[w["key"]] == w["not"]:
                d[f["key"]] = f.get("offValue", f["default"])
    return s, d, errs
