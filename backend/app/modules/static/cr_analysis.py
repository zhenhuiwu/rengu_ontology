"""Validation stores actual static dependencies and a bounded, permission-scoped metric replay."""

import hashlib
import json
import re
from decimal import Decimal, localcontext

from sqlalchemy import delete, select

from app.core.errors import AppError
from app.modules.static.metric_trial import evaluate_metric
from app.modules.static.models import (
    CrImpact,
    CrTrial,
    Dimension,
    Knowledge,
    KnowledgeApply,
    LinkType,
    Metric,
    ObjectType,
)

LABEL = {
    "object": "对象类型",
    "link": "关系",
    "metric": "指标",
    "dimension": "维度",
    "knowledge": "业务知识",
    "action": "动作类型",
    "event": "事件类型",
    "rule": "规则",
    "quality": "质量检查",
    "function": "函数",
}


def trial_access(user):
    if user is None:
        return None
    data = {"rows": user.row_filters, "attrs": user.attrs, "metrics": user.func.get("metrics")}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


async def definition_impacts(session, targets):
    """Actual persisted structural references; display-only ref_rows/usage counters are not dependencies."""
    from app.modules.static.object_usage_pages import pattern

    objects = list(
        await session.execute(
            select(ObjectType.id, ObjectType.code, ObjectType.display_name, ObjectType.api_name)
        )
    )
    links = list(
        await session.execute(
            select(LinkType.code, LinkType.display_name, LinkType.from_code, LinkType.to_code)
        )
    )
    dims = list(await session.execute(select(Dimension.code, Dimension.display_name, Dimension.object_id)))
    metrics = list(
        await session.execute(
            select(
                Metric.code,
                Metric.display_name,
                Metric.api_name,
                Metric.formula,
                Metric.dims,
                Metric.variants,
            )
        )
    )
    knowledge = list(await session.execute(select(Knowledge.code, Knowledge.name)))
    applies = list(
        await session.execute(
            select(KnowledgeApply.target_kind, KnowledgeApply.target_code, Knowledge.code).join(
                Knowledge, Knowledge.id == KnowledgeApply.knowledge_id
            )
        )
    )
    nodes = {}
    for _, code, name, api in objects:
        nodes[("object", code)] = (name, [code, name, api])
    for code, name, *_ in links:
        nodes[("link", code)] = (name, [code, name])
    for code, name, _ in dims:
        nodes[("dimension", code)] = (name, [code, name])
    for code, name, api, *_ in metrics:
        nodes[("metric", code)] = (name, [code, name, api])
    for code, name in knowledge:
        nodes[("knowledge", code)] = (name, [code, name])
    edges = {}

    def dependency(source, dependent, note):
        if source != dependent:
            edges.setdefault(source, []).append((dependent, note))

    for code, _, frm, to in links:
        for endpoint in (frm, to):
            dependency(("object", endpoint), ("link", code), "关系端点引用")
    object_codes = {row.id: row.code for row in objects}
    for code, _, object_id in dims:
        if object_id in object_codes:
            dependency(("object", object_codes[object_id]), ("dimension", code), "维度归属对象")
    for code, _, _, formula, allowed, variants in metrics:
        for key, (_, aliases) in nodes.items():
            if key[0] in ("object", "metric") and formula and re.search(pattern(aliases), formula):
                dependency(key, ("metric", code), "公式明确提及该定义，需核对口径")
            if key[0] == "dimension" and any(name in (allowed or []) for name in aliases):
                dependency(key, ("metric", code), "允许维度引用")
        for variant in variants or []:
            if isinstance(variant, dict) and variant.get("code"):
                dependency(("metric", variant["code"]), ("metric", code), "显式口径变体引用")
    for kind, code, term in applies:
        dependency(
            ("object" if kind.value == "object-type" else "metric", code),
            ("knowledge", term),
            "知识适用范围引用",
        )
        if any(t.target_kind == "knowledge" and t.target_id == term for t in targets):
            dependency(
                ("knowledge", term),
                ("object" if kind.value == "object-type" else "metric", code),
                "适用的业务知识发生改动",
            )
    direct_targets = {(target.target_kind, target.target_id): target for target in targets}
    direct_keys = list(direct_targets)
    direct = [
        {
            "kind": LABEL.get(kind, kind),
            "name": nodes.get((kind, code), (target.target_name or code, []))[0],
            "note": "变更单直接目标",
            "hover": {"kind": kind, "id": code},
        }
        for kind, code in direct_keys
        for target in [direct_targets[(kind, code)]]
    ]
    seen, queue, definitions = set(direct_keys), list(direct_keys), []
    while queue:
        source = queue.pop(0)
        for dependent, note in edges.get(source, []):
            if dependent in seen or dependent not in nodes:
                continue
            seen.add(dependent)
            queue.append(dependent)
            kind, code = dependent
            definitions.append(
                {
                    "kind": LABEL[kind],
                    "name": nodes[dependent][0],
                    "note": note,
                    "hover": {"kind": kind, "id": code},
                }
            )
    return {"direct": direct, "defs": definitions, "usage": []}


async def refresh_analysis(session, cr, targets, items, user):
    impact = await definition_impacts(session, targets)
    from app.contracts.dynamic import definition_usages

    affected = [row["hover"] for group in ("direct", "defs") for row in impact[group] if row.get("hover")]
    impact["usage"] = await definition_usages(session, affected)
    await session.execute(delete(CrImpact).where(CrImpact.cr_id == cr.id))
    for group, rows in impact.items():
        for row in rows:
            ref = row.get("hover") or {}
            session.add(
                CrImpact(
                    cr_id=cr.id,
                    grp=group,
                    kind=row["kind"],
                    name=row["name"],
                    note=row["note"],
                    ref_kind=ref.get("kind"),
                    ref_id=ref.get("id"),
                )
            )
    await session.execute(delete(CrTrial).where(CrTrial.cr_id == cr.id))
    data = dict(cr.detail or {})
    data.update(impact=impact, analysisScope="静态结构与真实规则/生命周期引用；未连接的运行依赖不推测数量")
    for key in ("trial", "trialUnavailable", "trialActor", "trialAccess"):
        data.pop(key, None)
    metric_codes = list(
        dict.fromkeys(
            item.target_id
            for item in items
            if item.target_kind == "metric"
            and item.section == "def"
            and item.item_key in ("formula", "version")
        )
    )
    if not metric_codes:
        data["trialUnavailable"] = "本次没有已保存的指标公式或口径版本改动，未执行试跑"
    elif len(metric_codes) != 1:
        # shortcut: cr_trial has one value per month, add a metric key before comparing multiple metrics.
        data["trialUnavailable"] = "一张变更单含多个指标口径，无法把不同单位合成一条试跑；请分别试算各指标"
    elif user is None:
        data["trialUnavailable"] = "缺少试算用户，未执行业务数据读取"
    else:
        code = metric_codes[0]
        try:
            after = await evaluate_metric(session, user, code, cr_id=cr.code, days=90, period="month")
            target = next(t for t in targets if t.target_kind == "metric" and t.target_id == code)
            before = (
                {"rows": []}
                if target.is_new
                else await evaluate_metric(session, user, code, days=90, period="month")
            )
            old = {row["date"][:7]: row["value"] for row in before["rows"]}
            new = {row["date"][:7]: row["value"] for row in after["rows"]}
            rows = []
            scope = f"{after['scope']} · {user.id}"
            for month in sorted(old.keys() | new.keys()):
                a, b = old.get(month), new.get(month)
                if any(value is not None and len(value) > 64 for value in (a, b)):
                    raise AppError(
                        "试跑数值超过已有存储长度，未记录对比",
                        code="METRIC_TRIAL_UNAVAILABLE",
                        status_code=422,
                    )
                with localcontext() as context:
                    context.prec = 128
                    delta = str(Decimal(b) - Decimal(a)) if a is not None and b is not None else "--"
                if len(delta) > 32:
                    raise AppError(
                        "试跑差异超过已有存储长度，未记录对比",
                        code="METRIC_TRIAL_UNAVAILABLE",
                        status_code=422,
                    )
                row = {
                    "period": month,
                    "before": str(a) if a is not None else "--",
                    "after": str(b) if b is not None else "--",
                    "delta": delta,
                }
                rows.append(row)
            for row in rows:
                session.add(
                    CrTrial(
                        cr_id=cr.id,
                        period=row["period"],
                        before_val=row["before"],
                        after_val=row["after"],
                        delta=row["delta"],
                        scope=f"{user.id} · {after['scope'].split(' · ', 1)[0]}",
                    )
                )
            data["trial"] = {"title": f"{code} · 近90日按月对比（{scope}）", "rows": rows}
            data["trialActor"] = user.id
            data["trialAccess"] = trial_access(user)
            if not rows:
                data["trialUnavailable"] = "已按真实口径查询，所选范围没有可见数据；没有补造月份或零值"
        except AppError as error:
            if error.code not in (
                "METRIC_TRIAL_UNAVAILABLE",
                "VALIDATION_FAILED",
                "NO_PERMISSION",
                "NOT_FOUND",
            ):
                raise
            data["trialUnavailable"] = f"{code} 未执行试跑：{error.message}（{error.code}）"
    cr.detail = data
    return data.get("trialUnavailable") if metric_codes else None


def visible_analysis(detail, actor):
    """Persisted business values are scoped to their actual trial actor, including preview readers."""
    if detail.get("trialActor") and (
        detail["trialActor"] != getattr(actor, "id", None) or detail.get("trialAccess") != trial_access(actor)
    ):
        return {key: value for key, value in detail.items() if key != "trial"} | {
            "trialUnavailable": "该试跑按其他用户或已变更的数据权限生成；当前用户不能读取其业务数值"
        }
    return detail
