"""QA 元数据：运行时读数据库；旧 JSON 读取仅供种子与尚未转换的旧接口。"""

import json
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import require_ontology_id
from app.core.config import get_settings

# 旧语义计划的传输键，只转换标识，不包含名称、版本或答案。
METRIC_CODES = {
    "margin": "gross_margin",
    "orders": "order_cnt",
    "ship": "ship_ontime",
    "refund": "refund_rate",
    "repeat": "repeat_sales",
    "repeat_tax": "repeat_sales_tax",
}
METRIC_KEYS = {v: k for k, v in METRIC_CODES.items()}
Metrics = dict[str, dict[str, Any]]
TIMES = ["昨天", "本周", "上周", "本月至今", "上个月", "近 7 天", "近 30 天", "近 6 个月", "今年"]


def metric_key(key: str, ontology_id: int = 1) -> str:
    return METRIC_KEYS.get(key, key) if ontology_id == 1 else key


def metric_of(metrics: Metrics, key: str | None) -> dict[str, Any] | None:
    if key in metrics:
        return metrics[key]
    legacy = metrics.get(metric_key(key or ""))
    return legacy if legacy and legacy.get("code") == key else None


async def directory(
    session: AsyncSession, ver: str | None = None, *, include_dims: bool = True
) -> tuple[Metrics, list[str]]:
    """一次加载请求/运行所需目录；历史缺节返回空，不能借用当前定义。"""
    from app.modules.static.dimension_validation import query_dimension_names
    from app.modules.static.models import Dimension, DimLevel, Metric
    from app.modules.static.repo import pending_change_codes
    from app.modules.versioning.repo import ReleaseRepo

    ontology_id = require_ontology_id(session)
    if ver:
        release = await ReleaseRepo(session).by_ver_or_404(ver)
        snap = release.snapshot or {}
        rows = list((snap.get("metrics") or {}).values())
        dimensions = list((snap.get("dimensions") or {}).values()) if include_dims else []
    else:
        pending = await pending_change_codes(session)
        rows = [
            {
                "id": m.code,
                "name": m.display_name,
                "version": m.version,
                "dimNames": m.dims or [],
                "cr": m.review_cr if m.review_cr in pending else None,
            }
            for m in await session.execute(
                select(
                    Metric.code, Metric.display_name, Metric.version, Metric.dims, Metric.review_cr
                ).order_by(Metric.seq, Metric.code)
            )
        ]
        # Read source presence, never load the object/property graph. Keep unbound names
        # so metric dimNames cannot reintroduce a standalone code table as a SQL dimension.
        dimensions_by_id = {}
        if include_dims:
            for did, name, object_id, status, level in await session.execute(
                select(Dimension.id, Dimension.display_name, Dimension.object_id, Dimension.status, DimLevel.name)
                .outerjoin(DimLevel, DimLevel.dimension_id == Dimension.id)
                .order_by(Dimension.seq, Dimension.code, DimLevel.level_no)
            ):
                dimension = dimensions_by_id.setdefault(did, {
                    "name": name, "object": object_id is not None, "status": status.value, "levels": []
                })
                if level:
                    dimension["levels"].append({"name": level})
        dimensions = list(dimensions_by_id.values())
    metrics = {
        metric_key(m["id"], ontology_id): {
            "key": metric_key(m["id"], ontology_id),
            "code": m["id"],
            "name": m["name"],
            "version": m.get("version") or "",
            "cr": m.get("cr") if not ver else (m.get("review") or {}).get("cr"),
        }
        for m in rows
        if m.get("id") and m.get("name")
    }
    return metrics, query_dimension_names(dimensions, rows) if include_dims else []


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


def metric_label(key: str | None, metrics: Metrics) -> str:
    m = metric_of(metrics, key)
    return f"{m['name']} {m['version']}".strip() if m else f"{key or '--'}（指标不可用）"


def plan_text(plan: dict[str, Any] | None, metrics: Metrics) -> str:
    """跟前端 QaTuning planText 一致：「指标 vN · 按 维度、… · 时间 · 过滤 · 排序」。"""
    if not plan:
        return ""
    dims = plan.get("dims") or []
    parts = [
        metric_label(plan.get("metric"), metrics),
        ("按 " + "、".join(dims)) if dims else "汇总",
        plan.get("time"),
        plan.get("filter"),
        plan.get("sort"),
    ]
    return " · ".join(p for p in parts if p)
