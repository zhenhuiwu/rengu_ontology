"""问数里用到的动态层能力（全部进程内调用动态的服务，不读动态的表）：

- 单对象动态摘要：问题只点名一个对象实例时，取 `GET /object-types/{id}/objects/{objId}/dynamic-summary`
  同一个服务（`ObjectDynamicSummary`），进 prompt、回答（`answer.dynamicSummary`）和 W7 citations。
  门槛同告警引用：对象读取权限（object-search / object-detail，同该接口入口）；各段权限由动态服务按段判；
  API 密钥调用另由 mcp_tools 按 dynamic:read 兜底去掉。读失败 → 不带。
- 多对象动态摘要：问题点名了多个对象实例，或表格结果里出现了多个对象实例（code 列取值）时，逐个取同一个
  服务的摘要（最多 MULTI_SCAN 个），只留有未关闭告警的、按未关闭告警数倒序取前 MULTI_TOP 个，
  进 prompt / 回答（`answer.dynamicSummaries`）/ citations。门槛同单对象。
- 决策评估：问题是“某告警 / 某对象该怎么处理”或模型给出动作意图时，走 `POST /decisions:evaluate`
  同一个服务（`DecisionService.evaluate`），确认卡上列排序后的建议；caller 带 AI 会话 / 轮次，动态侧写
  decision_log。只建议、不暂存、不执行。读失败 / 没有建议 → 退回能力目录（capabilities）那条路。
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

SUMMARY_LIMIT = 5
MULTI_SCAN = 10  # 多对象：最多取这么多个对象的摘要
MULTI_TOP = 3  # 多对象：回答里列有告警的前几个
DECISION_LIMIT = 5
CODE_COL = "code"  # 对象实例编号列（动态摘要的 objId = 表的 code 列）
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{2,63}")
# “该怎么处理”类说法：通用词，不含任何业务动作名
_ADVICE_WORDS = (
    "怎么处理",
    "如何处理",
    "怎么办",
    "处理建议",
    "建议",
    "该做什么",
    "下一步",
    "要做什么",
    "需要做什么",
    "应该做",
    "怎么应对",
    "如何应对",
    "what should",
    "recommend",
    "suggest",
    "next step",
)


def wants_advice(question: str) -> bool:
    q = question.lower()
    return any(w in q for w in _ADVICE_WORDS)


def _key(request: Any) -> Any:
    return getattr(getattr(request, "state", None), "api_key", None)


def key_scopes(request: Any) -> tuple[str, ...] | None:
    key = _key(request)
    return tuple(key.scopes) if key is not None else None


# ---------------------------------------------------------------- 单对象


async def single_object(
    session: AsyncSession, question: str, names: dict[str, list[str]]
) -> dict[str, str] | None:
    """问题只点名一个对象实例 → {kind, id, label}；点名多个 / 一个都没有 / 读失败 → None。"""
    objs = await named_objects(session, question, names)
    return objs[0] if len(objs) == 1 else None


async def named_objects(
    session: AsyncSession, question: str, names: dict[str, list[str]]
) -> list[dict[str, str]]:
    """问题点名的对象实例 [{kind, id, label}]（最多 MULTI_SCAN 个）；读失败 → []。"""
    try:
        async with session.begin_nested():
            return await _named_objects(session, question, names)
    except Exception:
        return []


def _gov(session: AsyncSession) -> dict[str, Any]:
    from app.common.ontology import require_ontology_id
    from app.common.sql_guard import governed_tables

    oid = require_ontology_id(session)
    return {
        g.table.name: g
        for g in governed_tables(oid, session).values()
        if not getattr(g, "inherit", False) and CODE_COL in g.table.c
    }


def _label_col(t: Any) -> Any:
    return t.c["name"] if "name" in t.c else t.c[CODE_COL]


async def _codes_in(session: AsyncSession, values: list[str]) -> dict[tuple[str, str], str]:
    """这些取值里哪些是某张可问表 code 列的取值 → {(对象类型, code): 名称}。"""
    found: dict[tuple[str, str], str] = {}
    if not values:
        return found
    from app.common.sql_guard import ensure_governed_tables

    await ensure_governed_tables(session)
    for g in _gov(session).values():
        t = g.table
        rows = await session.execute(
            sa.select(t.c[CODE_COL], _label_col(t)).where(t.c[CODE_COL].in_(values)).limit(MULTI_SCAN)
        )
        for code, label in rows:
            found[(g.object_type, str(code))] = str(label or code)
        if len(found) >= MULTI_SCAN:
            break
    return found


def _as_list(found: dict[tuple[str, str], str]) -> list[dict[str, str]]:
    return [{"kind": k, "id": c, "label": label} for (k, c), label in list(found.items())[:MULTI_SCAN]]


async def _named_objects(
    session: AsyncSession, question: str, names: dict[str, list[str]]
) -> list[dict[str, str]]:
    """两种点名（都从数据推，不写死）：问题里出现某张可问表 code 列的取值；或出现名称列的完整取值
    （去掉通用后缀也算，如“张江”对“张江店”），且该名称只对应一个 code。
    """
    from app.modules.qa.chain import _strip_suffix, name_suffix

    q = re.sub(r"\s", "", question)
    tokens = list(dict.fromkeys(_TOKEN.findall(question)))[:10]
    found = await _codes_in(session, tokens)
    from app.common.sql_guard import ensure_governed_tables

    await ensure_governed_tables(session)
    for name, g in _gov(session).items():
        t = g.table
        vals = names.get(name) or []
        suffix = name_suffix(vals)
        hits = [
            v for v in vals if v and (v in q or (len(core := _strip_suffix(v, suffix)) >= 2 and core in q))
        ]
        # 名字互相包含时（“张江店”与“张江二店”）只认最长的那个
        hits = [v for v in hits if not any(v != w and v in w for w in hits)]
        for hit in hits[:MULTI_SCAN]:
            rows = (
                await session.execute(sa.select(t.c[CODE_COL]).where(_label_col(t) == hit).limit(2))
            ).all()
            if len(rows) == 1:
                found[(g.object_type, str(rows[0][0]))] = hit
        if len(found) >= MULTI_SCAN:
            break
    return _as_list(found)


async def result_objects(session: AsyncSession, rows: list[Any]) -> list[dict[str, str]]:
    """表格结果里出现的对象实例（文本单元格是某张可问表的 code 取值）；读失败 → []。"""
    vals = list(
        dict.fromkeys(
            str(v)
            for r in rows
            for v in (r if isinstance(r, list | tuple) else ())
            if isinstance(v, str) and v
        )
    )[:200]
    try:
        async with session.begin_nested():
            return _as_list(await _codes_in(session, vals))
    except Exception:
        return []


async def object_summary(
    session: AsyncSession, user: Any, obj: dict[str, str] | None, request: Any = None
) -> dict[str, Any] | None:
    """动态摘要（同 dynamic-summary 接口的服务）；没有对象读取权限 / 不可见 / 读失败 → None。"""
    if not obj:
        return None
    from app.core.errors import AppError
    from app.modules.dynamic.catalog import get_list_catalog
    from app.modules.dynamic.summary import ObjectDynamicSummary
    from app.modules.qa.object_source import check_object_read

    try:
        check_object_read(user)
    except AppError:
        return None
    try:
        async with session.begin_nested():  # savepoint：读失败不弄坏请求事务
            svc = ObjectDynamicSummary(
                session, user, await get_list_catalog(session), None, api_scopes=key_scopes(request)
            )
            raw = await svc.get(obj["kind"], obj["id"], limit=SUMMARY_LIMIT)
    except Exception:
        return None
    return brief(raw)


async def objects_summary(
    session: AsyncSession, user: Any, objs: list[dict[str, str]], request: Any = None
) -> list[dict[str, Any]]:
    """多个对象的动态摘要：只留有未关闭告警的，按未关闭告警数倒序取前 MULTI_TOP 个。"""
    if len(objs) < 2:
        return []
    from app.core.errors import AppError
    from app.modules.qa.object_source import check_object_read

    try:
        check_object_read(user)
    except AppError:
        return []
    out = []
    for obj in objs[:MULTI_SCAN]:
        s = await object_summary(session, user, obj, request)  # 不可见 / 读失败 → None，跳过
        if s and s["alerts"].get("available") and (s["alerts"].get("openCount") or 0) > 0:
            out.append(s)
    out.sort(key=lambda s: -(s["alerts"].get("openCount") or 0))
    return out[:MULTI_TOP]


def multi_text(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    parts = [
        f"{s.get('objLabel') or s.get('id')}（{s.get('objKindLabel') or s.get('objKind')}）"
        f"未关闭告警 {s['alerts'].get('openCount') or 0} 条"
        for s in items
    ]
    return "动态提示：" + "；".join(parts)


def multi_lines(items: list[dict[str, Any]]) -> list[str]:
    out = []
    for s in items:
        out.extend(summary_lines(s))
    return out


def _section(raw: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    raw = raw or {}
    if not raw.get("available"):
        return {"available": False, "reason": raw.get("reason") or "unavailable"}
    return {"available": True, **{k: raw.get(k) for k in keys if k in raw}}


def brief(raw: dict[str, Any]) -> dict[str, Any]:
    """回答里带的精简摘要（明细到对象详情 / 接口看）。"""
    life = raw.get("lifecycle") or {}
    lifecycle = _section(life, ("defined", "changeTotal", "illegalTotal"))
    if lifecycle["available"]:
        lifecycle["state"] = (life.get("current") or {}).get("state")
    latest = [
        {k: a.get(k) for k in ("id", "ruleId", "level", "status", "triggeredAt") if k in a}
        for a in ((raw.get("alerts") or {}).get("latest") or [])[:3]
    ]
    alerts = _section(raw.get("alerts"), ("openCount", "byStatus"))
    if alerts["available"]:
        alerts["latest"] = latest
    return {
        "objKind": raw.get("objKind"),
        "objKindLabel": raw.get("objKindLabel"),
        "id": raw.get("id"),
        "objLabel": raw.get("objLabel"),
        "alerts": alerts,
        "ruleHits": _section(raw.get("ruleHits"), ("total",)),
        "lifecycle": lifecycle,
        "actionRuns": _section(raw.get("actionRuns"), ("total", "statusCounts")),
    }


def summary_text(s: dict[str, Any] | None) -> str:
    """一句话动态摘要（只写有权限的段）。"""
    if not s:
        return ""
    parts = []
    if s["alerts"].get("available"):
        parts.append(f"未关闭告警 {s['alerts'].get('openCount') or 0} 条")
    if s["ruleHits"].get("available"):
        parts.append(f"规则命中 {s['ruleHits'].get('total') or 0} 次")
    life = s["lifecycle"]
    if life.get("available") and life.get("defined") and life.get("state"):
        parts.append(f"生命周期「{life['state']}」")
    if s["actionRuns"].get("available"):
        parts.append(f"相关动作 {s['actionRuns'].get('total') or 0} 条")
    if not parts:
        return ""
    return (
        f"{s.get('objLabel') or s.get('id')}（{s.get('objKindLabel') or s.get('objKind')}）动态："
        + "，".join(parts)
    )


def summary_lines(s: dict[str, Any] | None) -> list[str]:
    if not s or not (text := summary_text(s)):
        return []
    out = [f"- {text}"]
    for a in s["alerts"].get("latest") or []:
        out.append(f"- 告警 {a.get('id')} [{a.get('level')}] 规则 {a.get('ruleId')}，{a.get('status')}")
    return out


# ---------------------------------------------------------------- 决策评估


def has_type(decision: dict[str, Any] | None, type_id: str | None) -> bool:
    return bool(decision) and any(
        (r.get("actionType") or {}).get("id") == type_id for r in decision.get("recommendations") or []
    )


def decision_target(
    question: str,
    alerts: list[dict[str, Any]],
    obj: dict[str, str] | None,
    proposal: dict[str, Any] | None = None,
) -> dict[str, str] | None:
    """评估对象：问题点名的告警 > 唯一一条相关告警 > 动作意图里的目标对象 > 问题点名的唯一对象。"""
    q = question.lower()
    named = [a for a in alerts if a.get("id") and str(a["id"]).lower() in q]
    if len(named) == 1:
        return {"alertId": named[0]["id"]}
    if obj:
        mine = [a for a in alerts if a.get("objKind") == obj["kind"] and a.get("objId") == obj["id"]]
        if len(mine) == 1:
            return {"alertId": mine[0]["id"]}
    elif len(alerts) == 1:
        return {"alertId": alerts[0]["id"]}
    if proposal and proposal.get("objKind") and proposal.get("objId"):
        return {"objectTypeId": proposal["objKind"], "objectId": proposal["objId"]}
    if obj:
        return {"objectTypeId": obj["kind"], "objectId": obj["id"]}
    return None


async def evaluate(
    session: AsyncSession,
    user: Any,
    target: dict[str, str] | None,
    caller: dict[str, Any] | None,
    request: Any = None,
) -> dict[str, Any] | None:
    """同 `POST /decisions:evaluate`（门槛同该接口：alerts 查看；密钥另需 dynamic:read）。

    caller = {aiSessionId, aiRoundId, agent}：动态侧据此把 caller_kind 记成 ai。
    试运行：人工会话照跑；密钥没有 actions:dry-run 时传 dryRun=false（接口对这种密钥会 403）。
    没有建议 / 读失败 → None（调用方退回能力目录）。
    """
    if not target:
        return None
    from app.common.auth import check_func
    from app.core.errors import AppError
    from app.modules.dynamic.catalog import get_catalog
    from app.modules.dynamic.decisions import DRY_RUN_SCOPE, DecisionEvaluateIn, DecisionService

    scopes = key_scopes(request)
    if scopes is not None and "dynamic:read" not in scopes:
        return None
    try:
        check_func(user, "alerts", "view")
    except AppError:
        return None
    body = DecisionEvaluateIn.model_validate(
        {
            **target,
            "caller": {"agent": "qa", **{k: v for k, v in (caller or {}).items() if v}},
            "dryRun": scopes is None or DRY_RUN_SCOPE in scopes,
            "limit": DECISION_LIMIT,
        }
    )
    req = request if request is not None else SimpleNamespace(state=SimpleNamespace(api_key=None))
    try:
        async with (
            session.begin_nested()
        ):  # savepoint：评估失败不弄坏请求事务；成功则 decision_log 随轮次提交
            out = await DecisionService(session, user, await get_catalog(session, user), None, req).evaluate(
                body
            )
    except Exception:
        return None
    return out if out.get("recommendations") else None


def _check_of(rec: dict[str, Any]) -> dict[str, Any]:
    dry = rec.get("dryRun") or {}
    failed = [
        {"name": p.get("name") or p.get("api"), "msg": f"参数「{p.get('name') or p.get('api')}」待填写"}
        for p in rec.get("unfilledParams") or []
    ]
    if dry.get("ran"):
        failed = list(dry.get("failed") or []) + failed
        err = dry.get("error")
        if err:
            failed.append({"name": "试运行", "msg": str(err.get("message") or err.get("code"))[:200]})
        out = {
            "passed": bool(dry.get("passed")) and not failed,
            "checked": dry.get("checked") or 0,
            "failed": failed,
        }
        if dry.get("humanChecks"):  # 外部 Webhook 动作：留给确认人人工核对的条件（不算失败）
            out["humanChecks"] = list(dry["humanChecks"])
        return out
    why = {"no-permission": "没有动作类型查看权限，未试运行", "key-allowlist": "密钥不允许该动作，未试运行"}
    reason = why.get(dry.get("reason"), "未试运行")
    return {"passed": False, "checked": 0, "failed": [*failed, {"name": "试运行", "msg": reason}]}


def _rec_view(rec: dict[str, Any]) -> dict[str, Any]:
    pol = rec.get("policy") or {}
    return {
        "rank": rec.get("rank"),
        "score": rec.get("score"),
        "actionType": rec.get("actionType"),
        "params": rec.get("params") or {},
        "paramSources": rec.get("paramSources") or {},
        "unfilledParams": rec.get("unfilledParams") or [],
        "dryRun": _check_of(rec),
        "policy": {
            "mode": pol.get("mode", "confirm"),
            "aiAllowed": pol.get("callerAllowed", True) is not False,
        },
        "rationale": [r.get("text") for r in rec.get("reasons") or [] if r.get("text")],
        "handoff": rec.get("handoff"),
    }


def decision_choice(
    decision: dict[str, Any], proposal: dict[str, Any] | None = None
) -> tuple[dict[str, Any], int, bool]:
    """主建议：第一名；模型给了动作意图且该动作在候选里时以它为主。

    返回 (动作意图, 候选下标, 是否用模型参数补了决策没填上的参数——补了要重新试运行)。
    模型参数只补待填项，不覆盖规则映射的预填值。
    """
    recs = decision["recommendations"]
    want = (proposal or {}).get("type")
    i = next((n for n, r in enumerate(recs) if (r.get("actionType") or {}).get("id") == want), 0)
    raw = recs[i]
    params = dict(raw.get("params") or {})
    unfilled = {p.get("api") for p in raw.get("unfilledParams") or []}
    if want and want == raw["actionType"]["id"]:
        params |= {
            k: v for k, v in (proposal.get("params") or {}).items() if k in unfilled and v not in (None, "")
        }
    body = (raw.get("handoff") or {}).get("body") or {}
    intent = {
        "type": raw["actionType"]["id"],
        "name": raw["actionType"]["name"],
        "objKind": body.get("objKind") or decision.get("objKind"),
        "objId": body.get("objId") or decision.get("objId"),
        "objLabel": body.get("objLabel"),
        "params": params,
        "reason": "；".join(r.get("text") for r in raw.get("reasons") or [] if r.get("text"))[:200],
    }
    return intent, i, params != (raw.get("params") or {})


def decision_snapshot(
    decision: dict[str, Any], proposal: dict[str, Any] | None = None, check: dict[str, Any] | None = None
) -> dict[str, Any]:
    """决策评估 → 建议动作卡（形状同能力目录那条路的 action 卡，多 `action.decision` 排序候选）。

    `check`：补了参数后重新试运行的结果（`intent.dry_run`）；不给就用决策评估里的试运行结果，
    并把已补的待填项去掉。
    """
    from app.modules.qa.intent import action_snapshot

    recs = [_rec_view(r) for r in decision["recommendations"]]
    intent, i, filled = decision_choice(decision, proposal)
    top = recs[i]
    if check is None:
        check = top["dryRun"]
        if filled:
            done = _names_of(top, set(intent["params"]) - set(top["params"]))
            failed = [f for f in check["failed"] if f.get("name") not in done]
            ran = (decision["recommendations"][i].get("dryRun") or {}).get("passed")
            check = {**check, "failed": failed, "passed": bool(ran) and not failed}
    snap = action_snapshot(intent, check, top["policy"])
    ans = snap["answer"]
    ans["basis"] = {
        "caliber": "决策评估（动态 decisions:evaluate）→ 排序建议；只建议，确认后由动作中心执行",
        "perm": "提交时按动作权限再校验",
    }
    if others := [r for r in recs if r is not top]:
        ans["text"] += (
            "其它候选：" + "、".join(f"{r['rank']}. {r['actionType']['name']}" for r in others) + "。"
        )
    ans["action"]["decision"] = {
        "decisionId": decision.get("decisionId"),
        "alertId": decision.get("alertId"),
        "objKind": decision.get("objKind"),
        "objId": decision.get("objId"),
        "recommendations": recs,
    }
    return snap


def _names_of(rec: dict[str, Any], apis: set[str]) -> set[str]:
    return {p.get("name") or p.get("api") for p in rec["unfilledParams"] if p.get("api") in apis}
