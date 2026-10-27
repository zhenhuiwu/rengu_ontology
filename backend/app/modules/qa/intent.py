"""问数里的动作意图与告警上下文（只读，不改 action / dynamic 代码）。

- 动作意图：问题要“做一件事”时，模型输出 ```json {"action": {...}}```；这里只调动作中心已有的
  `:dry-run` 校验（`action.checker.definition` + `DatabaseSubmissionChecker`，同 `POST
  /action-types/{id}:dry-run`，不建 action_run、不调外部），回一张**建议**确认卡。
- 候选动作和执行策略都取自能力目录（`static.capabilities`，同 `GET /meta/capabilities`）：
  suggest → 卡上只给建议、不能发起；confirm / auto →
  卡可经 `POST /qa/turns/{tid}/action:stage` 暂存（来源 问数，确认人 = 当前用户，**不走 apply_policy**，
  auto 也不自动执行），再由用户在确认卡 / 动作中心 `/action-runs/{code}:confirm`。问数自己从不执行。
- 告警：问题提到告警 / 异常，或点名的对象有未处理告警时，把可见的告警（`dynamic.row_scope.scoped_instances`
  同款行级可见性）作为只读上下文放进 prompt，并随回答带 `alerts` 引用。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ACTION_LIMIT = 30  # prompt 里最多列多少个动作类型
ALERT_LIMIT = 8
_ID_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{1,6}-?[0-9]{3,}(?:-[0-9]+)*(?![A-Za-z0-9])")
_ALERT_WORDS = ("告警", "预警", "报警", "异常", "命中", "规则", "alert", "warning", "anomal")


# ---------------------------------------------------------------- 动作意图


async def action_catalog(session: AsyncSession, user: Any, request: Any = None) -> list[dict[str, Any]]:
    """候选动作 = 能力目录（`static.capabilities`，同 `GET /meta/capabilities`）的 actions 节：

    当前版本已发布 / 变更中、已绑定对象、调用者有动作类型查看权限；带执行策略（action.policy 公开读）。
    看不到该节（omitted）/ 目标对象一行都看不到 / 读失败 → 不列。不再自建动作目录。
    """
    from app.modules.static.capabilities import capabilities

    try:
        async with session.begin_nested():  # savepoint：读失败不弄坏请求事务
            cat = await capabilities(session, user, request)
    except Exception:
        return []
    out = []
    for a in cat.get("actions") or []:
        if a.get("rowAccess") == "none":
            continue
        out.append(
            {
                "id": a["id"],
                "name": a["name"],
                "targetKind": a.get("object"),
                "params": [
                    {"api": p["api"], "name": p["name"], "type": p["type"], "required": bool(p["required"])}
                    for p in a.get("params") or []
                ],
                "policy": policy_view(a.get("policy")),
            }
        )
        if len(out) >= ACTION_LIMIT:
            break
    return out


def policy_view(raw: Any) -> dict[str, Any]:
    """能力目录 / 动作版本里的策略 → 问数卡用的 {mode, aiAllowed}（问数暂存的调用方类型 = AI）。"""
    from app.modules.action import policy as action_policy

    pol = action_policy.stored(raw)
    caller = action_policy.CALLER_OF_SOURCE["问数"]
    return {"mode": pol.mode, "aiAllowed": pol.callers is None or caller in pol.callers}


def action_lines(catalog: list[dict[str, Any]]) -> list[str]:
    lines = []
    for a in catalog:
        ps = "，".join(
            f"{p['api']}（{p['name']}，{p['type']}{'，必填' if p['required'] else ''}）" for p in a["params"]
        )
        target = a.get("targetKind") or "无"
        lines.append(f"- {a['id']} {a['name']}：目标对象类型 {target}；参数 {ps or '无'}")
    return lines


ACTION_GUIDE = (
    "\n4. 用户要求**执行一件事**（发起、发放、调整、通知、下架、关闭等业务操作），"
    "且“可用动作”里有对应动作时，"
    '不写 SQL，只输出 ```json {"action": {"type": "动作编号", "objKind": "目标对象类型", '
    '"objId": "目标对象编号", "objLabel": "目标对象名称", "params": {"参数api": "值"}, '
    '"reason": "一句话说明"}} ```；目标对象要用库里真实的编号和名称（不知道就留空，由用户在确认卡里补）；'
    "这只是建议，系统不会自动执行。没有对应动作的写操作仍按 NOT_SELECT 拒答。\n"
)


def parse_action(obj: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    a = obj.get("action")
    if not isinstance(a, dict):
        return None
    known = {c["id"]: c for c in catalog}
    by_name = {c["name"]: c for c in catalog}
    key = str(a.get("type") or a.get("id") or "").strip()
    c = known.get(key) or by_name.get(key)
    if c is None:
        return None
    params = a.get("params") if isinstance(a.get("params"), dict) else {}
    allowed = {p["api"] for p in c["params"]}
    return {
        "type": c["id"],
        "name": c["name"],
        "objKind": (str(a.get("objKind") or c.get("targetKind") or "") or None),
        "objId": str(a.get("objId") or "")[:64] or None,
        "objLabel": str(a.get("objLabel") or "")[:200] or None,
        "params": {k: v for k, v in params.items() if k in allowed},
        "reason": str(a.get("reason") or "")[:200],
    }


def _params_text(params: dict[str, Any]) -> str:
    return "，".join(f"{k}={v}" for k, v in params.items()) if params else ""


async def dry_run(session: AsyncSession, user: Any, intent: dict[str, Any]) -> dict[str, Any]:
    """同 `POST /action-types/{id}:dry-run`：只读校验，不建 action_run。校验本身报错 → passed=False。"""
    from app.common.context import ViewContext
    from app.modules.action.checker import DatabaseSubmissionChecker, definition

    try:
        async with session.begin_nested():
            value = await definition(session, intent["type"], ViewContext(), "published", None)
            if intent.get("objKind") and intent["objKind"] != value.get("targetKind"):
                intent["objKind"] = value.get("targetKind")
            if not intent.get("objId"):
                return {
                    "passed": False,
                    "checked": 0,
                    "failed": [{"name": "目标对象", "msg": "请指定目标对象"}],
                }
            res = await DatabaseSubmissionChecker(session, user).run(
                value,
                {
                    "params": intent["params"],
                    "objKind": intent.get("objKind"),
                    "objId": intent["objId"],
                    "alertId": None,
                },
            )
            out = dict(res)
            if declared := await _declared(session, value):
                out["declaredEdits"] = declared
            return out
    except Exception as e:
        msg = getattr(e, "message", None) or str(e) or type(e).__name__
        return {"passed": False, "checked": 0, "failed": [{"name": "校验", "msg": str(msg)[:200]}]}


async def _declared(session: AsyncSession, value: dict[str, Any]) -> list[dict[str, Any]]:
    """外部 Webhook 通道动作的声明效果（action-event-api §18.2，同执行载荷 declaredEdits）；读失败 → []。"""
    from app.modules.integration.action_edits import declared_writeback

    try:
        async with session.begin_nested():
            return list(await declared_writeback(session, value) or [])
    except Exception:
        return []


async def declared_edits(session: AsyncSession, type_id: str) -> list[dict[str, Any]]:
    """按动作类型编号读已发布定义 → 声明效果（决策评估那条路用，试运行结果里没有这一项）。"""
    from app.common.context import ViewContext
    from app.modules.action.checker import definition

    try:
        async with session.begin_nested():
            value = await definition(session, type_id, ViewContext(), "published", None)
    except Exception:
        return []
    return await _declared(session, value)


async def with_declared(session: AsyncSession, snap: dict[str, Any]) -> dict[str, Any]:
    """动作确认卡补上声明效果（已有就不动）。"""
    act = (snap.get("answer") or {}).get("action")
    if not isinstance(act, dict) or not act.get("type") or "declaredEdits" in (act.get("dryRun") or {}):
        return snap
    if declared := await declared_edits(session, act["type"]):
        act.setdefault("dryRun", {})["declaredEdits"] = declared
        act["declaredSummary"] = declared_summary(declared)
        snap["answer"]["text"] = f"{snap['answer'].get('text') or ''}{act['declaredSummary']}。"
    return snap


def declared_summary(declared: list[dict[str, Any]]) -> str:
    """“由外部系统承担：编辑 商品（状态、最近操作人）；通知 企业微信 · 发起人”。"""
    parts = []
    for d in declared[:5]:
        what = " ".join(str(x) for x in (d.get("obj"), d.get("field") or d.get("target")) if x)
        parts.append(f"{d.get('kind') or '效果'} {what}".strip())
    more = f" 等 {len(declared)} 项" if len(declared) > 5 else ""
    return "由外部系统承担（本地不执行）：" + "；".join(parts) + more


def human_text(checks: list[dict[str, Any]]) -> str:
    names = "、".join(str(c.get("name") or c.get("rule") or "") for c in checks[:5])
    return f"另有 {len(checks)} 项提交条件需确认人人工核对（{names}）"


async def action_policy(session: AsyncSession, code: str) -> dict[str, Any]:
    """暂存前现读一次已发布版本的执行策略 → {mode, aiAllowed}（卡片上的策略来自能力目录）。

    读失败 / 没有策略 → 默认 confirm（和 action.policy.stored 一样不放宽；暂存时动作中心还会再 gate）。
    """
    from app.modules.action import policy as action_policy
    from app.modules.action.models import ActionType

    pol = action_policy.DEFAULT
    try:
        async with session.begin_nested():
            ver = await session.scalar(select(ActionType.version).where(ActionType.code == code))
            pol = await action_policy.load(session, code, ver)
    except Exception:
        pol = action_policy.DEFAULT
    return policy_view(pol.dump())


def can_stage(policy: dict[str, Any] | None) -> bool:
    p = policy or {}
    return p.get("mode", "confirm") != "suggest" and p.get("aiAllowed", True) is not False


def action_snapshot(
    intent: dict[str, Any], check: dict[str, Any], policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    """建议动作卡（kind=answer，card=action）。按执行策略决定能否发起；发起只暂存，人工确认后才执行。"""
    policy = policy or {"mode": "confirm", "aiAllowed": True}
    stageable = can_stage(policy)
    if policy.get("mode") == "suggest":
        how = "该动作执行策略为“只给建议”，不能从问数发起，可到动作中心试运行查看"
    elif not stageable:
        how = "该动作执行策略不允许问数（AI）发起，请到动作中心手动发起"
    else:
        how = "点“发起动作”只会暂存到动作中心，需你确认后才提交，问数不会自动执行"
    stage = {
        "typeId": intent["type"],
        "objKind": intent.get("objKind"),
        "objId": intent.get("objId") or "",
        "objLabel": intent.get("objLabel") or intent.get("objId") or "",
        "params": _params_text(intent["params"]) or None,
    }
    passed = bool(check.get("passed"))
    tail = (
        "提交条件已满足"
        if passed
        else "提交条件未满足："
        + "；".join(str(f.get("msg") or f.get("name") or "") for f in check.get("failed") or [])
    )
    human = list(check.get("humanChecks") or [])  # 外部 Webhook 动作：解释器判不了、留给确认人的条件
    declared = list(check.get("declaredEdits") or [])
    if human:
        tail = f"{tail}；{human_text(human)}"
    dry_run: dict[str, Any] = {
        "passed": passed,
        "checked": int(check.get("checked") or 0),
        "failed": list(check.get("failed") or []),
    }
    if human:
        dry_run["humanChecks"] = human
    if declared:
        dry_run["declaredEdits"] = declared
    return {
        "answer": {
            "card": "action",
            "text": f"建议执行「{intent['name']}」"
            + (f"（{intent['objLabel'] or intent['objId']}）" if intent.get("objId") else "")
            + f"。{tail}。{how}。"
            + (f"{declared_summary(declared)}。" if declared else ""),
            "basis": {"caliber": "动作意图 → 动作中心 dry-run 校验", "perm": "提交时按动作权限再校验"},
            "action": {
                "type": intent["type"],
                "name": intent["name"],
                "reason": intent.get("reason") or "",
                "params": intent["params"],
                "dryRun": dry_run,
                **({"humanChecks": human} if human else {}),
                **({"declaredSummary": declared_summary(declared)} if declared else {}),
                "stage": stage,
                "mode": "suggest",  # 问数只建议；发起 = 暂存待人工确认
                "policy": policy.get("mode", "confirm"),  # 动作执行策略 suggest / confirm / auto
                "canStage": stageable,
            },
        }
    }


def action_json(intent: dict[str, Any]) -> str:
    return json.dumps(intent, ensure_ascii=False)


# ---------------------------------------------------------------- 告警（只读）


def wants_alerts(question: str) -> bool:
    q = question.lower()
    return any(w in q for w in _ALERT_WORDS)


async def related_alerts(
    session: AsyncSession, user: Any, question: str, names: list[str] | None = None
) -> list[dict[str, Any]]:
    """未处理 / 处理中的告警（只读）：问题提到告警类词 → 最近的；否则只取点名对象（anchors）的告警。

    走动态模块的告警服务（同 `GET /alerts`，进程内调用）：行级范围、维度可见性都按它的口径；
    当前用户没有告警页查看权限（alerts:view）就不引用。不直接读动态的表。
    """
    from app.common.auth import check_func
    from app.common.pagination import PageParams
    from app.core.errors import AppError
    from app.modules.dynamic.catalog import get_list_catalog
    from app.modules.dynamic.service import AlertService

    names = [n for n in dict.fromkeys(names or []) if n]
    broad = wants_alerts(question)
    # 问题里点名的编号（如告警编号）：最近 N 条里未必有，单独按关键字查
    ids = list(dict.fromkeys(_ID_TOKEN.findall(question)))[:3]
    if not broad and not names and not ids:
        return []
    try:
        check_func(user, "alerts", "view")
    except AppError:
        return []
    out: dict[str, dict[str, Any]] = {}
    try:
        async with session.begin_nested():  # savepoint：读失败不弄坏请求事务
            svc = AlertService(session, user, await get_list_catalog(session), None)  # 只读：不用 ports
            for kw in [*ids, *([None] if broad else names[:5])]:
                for status in ("open", "doing"):
                    res = await svc.search(
                        status=status,
                        level=None,
                        rule_id=None,
                        kw=kw,
                        page=PageParams(page=1, size=ALERT_LIMIT),
                    )
                    for x in res.items:
                        out.setdefault(x.id, _alert_ref(x))
    except Exception:
        return []
    named = {i.lower() for i in ids}
    ordered = sorted(out.values(), key=lambda a: a["triggeredAt"] or "", reverse=True)
    ordered.sort(key=lambda a: str(a.get("id") or "").lower() not in named)  # 点名的排前面（稳定排序）
    return ordered[:ALERT_LIMIT]


def _alert_ref(x: Any) -> dict[str, Any]:
    return {
        "id": x.id,
        "rule": x.rule_id,
        "level": x.level,
        "objKind": x.obj_kind,
        "objId": x.obj_id,
        "objLabel": x.obj_label,
        "value": x.value,
        "threshold": x.threshold,
        "status": x.status,
        "triggeredAt": x.triggered_at.isoformat() if x.triggered_at else None,
    }


def alert_lines(alerts: list[dict[str, Any]]) -> list[str]:
    return [
        f"- {a['id']} [{a['level']}] {a['objLabel']}（{a['objKind']} {a['objId']}）规则 {a['rule']}："
        f"值 {a['value']}，阈值 {a['threshold']}，{a['status']}，触发于 {a['triggeredAt']}"
        for a in alerts
    ]
