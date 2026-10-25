"""问数用到、但属于静态模块（指标目录 / 版本）的常量。

[推断] 静态的 metric / ontology_release 读取接口尚未合入骨架：先直接读 export-mock 导出的
seeds/json/qa/ 下的常量文件（T_METRICS、TUNING_DIMS、TUNING_TIMES、QA_METRIC、CUR_VERSION、
TUNING_CUTOFF …），只读、进程内缓存。静态模块合入后改成通过 app/contracts 的 Protocol 取。
"""

import json
from functools import lru_cache
from typing import Any

from app.core.config import get_settings


@lru_cache
def load(name: str) -> Any:
    path = get_settings().seeds_json_dir / "qa" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def t_metrics() -> dict[str, dict[str, Any]]:
    """调优页的指标表：key → {key, name, version, drawerId?, cr?}。"""
    return load("T_METRICS")


# 调优 key → 静态指标 code。T_METRICS 里没写 drawerId 的两个复购指标按 QA_METRIC 补。
_EXTRA_CODES = {"repeat": "repeat_sales", "repeat_tax": "repeat_sales_tax"}


def metric_code_of_key(key: str | None) -> str | None:
    if not key:
        return None
    m = t_metrics().get(key)
    if m is None:
        return None
    return m.get("drawerId") or _EXTRA_CODES.get(key) or key


def metric_by_name(name: str | None) -> dict[str, Any] | None:
    """按指标名（如「销售额（不含税）」）找调优指标；卡片 mock 只写了名字。"""
    if not name:
        return None
    for m in t_metrics().values():
        if m["name"] == name:
            return m
    return None


def version_int(ver: str | None) -> int | None:
    """ "v3" → 3；不是 vN 形式返回 None。"""
    if ver and ver.startswith("v") and ver[1:].isdigit():
        return int(ver[1:])
    return None


def metric_label(key: str | None) -> str:
    m = t_metrics().get(key or "")
    return f"{m['name']} {m['version']}" if m else (key or "")


def plan_text(plan: dict[str, Any] | None) -> str:
    """跟前端 QaTuning planText 一致：「指标 vN · 按 维度、… · 时间 · 过滤 · 排序」。"""
    if not plan:
        return ""
    dims = plan.get("dims") or []
    parts = [
        metric_label(plan.get("metric")),
        ("按 " + "、".join(dims)) if dims else "汇总",
        plan.get("time"),
        plan.get("filter"),
        plan.get("sort"),
    ]
    return " · ".join(p for p in parts if p)
