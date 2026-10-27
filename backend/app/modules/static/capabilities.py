"""能力目录（只读）：当前本体可被问数 / MCP 调用的对象类型、指标、函数、已绑定动作与规则。

- 只读当前空间的定义表（会话已按本体隔离），不支持 ?ver=（历史版本没有试算 / 执行能力）。
- 每一节按对应页面的 view 权限放行，没有权限的节列入 omitted，不报错；一节都看不到 → 403。
  API 密钥的功能权限已被 scope 收窄：只有 meta:read 的密钥只能看到 objects / metrics 两节。
- 敏感属性、隐藏属性不出现；行级范围不随目录下发，只给 rowAccess（all / scoped / none）提示。
- 函数不暴露代码、样例；动作给出执行策略（action/policy 的公开函数读）和机器执行接口。
"""

from typing import Any

from fastapi import Request
from sqlalchemy import select

from app.common.auth import CurrentUser, row_filter
from app.common.row_filter import is_deny_all
from app.core.config import api_prefix
from app.core.errors import AppError, Forbidden
from app.modules.action import policy as action_policy
from app.modules.action.models import ActionType, TypeStatus
from app.modules.dynamic.rules_models import Rule, RuleStatus
from app.modules.functions.models import FnParam, Function
from app.modules.static.metric_trial import READINESS_BATCH_MAX, metrics_readiness
from app.modules.static.models import Metric, MetricStatus, ObjectStatus, ObjectType

SECTIONS = (
    ("objects", "object-types"),
    ("metrics", "metrics"),
    ("functions", "functions"),
    ("actions", "action-types"),
    ("rules", "rules"),
)
NOTES = [
    "只含当前版本已发布（规则 / 动作含变更中）的定义；没有权限的节见 omitted。",
    "rowAccess 只是提示：none = 调用者看不到该对象任何行，scoped = 按行级范围过滤；"
    "取数时后台照常按调用者过滤。",
    "敏感属性、隐藏属性不列出。",
]


def _can_view(user: CurrentUser, page: str) -> bool:
    return user.func.get(page, "none") in ("view", "manage")


async def _row_access(user: CurrentUser, code: str | None, cache: dict[str, str]) -> str | None:
    if not code:
        return None
    if code not in cache:
        expr = await row_filter(user, code)
        cache[code] = "all" if expr is None else "none" if is_deny_all(expr) else "scoped"
    return cache[code]


def _visible_props(obj: ObjectType) -> list[dict[str, Any]]:
    return [
        {
            "api": p.api_name,
            "name": p.display_name,
            "type": p.data_type,
            **({"unit": p.unit} if p.unit else {}),
            "pk": p.is_pk,
            "title": p.is_title,
            "dimension": p.is_dimension,
            "filterable": p.filterable,
            "sortable": p.sortable,
        }
        for p in obj.properties
        if not p.is_sensitive and p.visibility != "hidden"
    ]


async def _objects(session, user, access, readable_only) -> tuple[list[dict], dict[str, ObjectType]]:
    rows = list(
        await session.scalars(
            select(ObjectType)
            .where(ObjectType.status == ObjectStatus.published)
            .order_by(ObjectType.seq, ObjectType.id)
        )
    )
    out = []
    for o in rows:
        row_access = await _row_access(user, o.code, access)
        if readable_only and row_access == "none":
            continue
        out.append(
            {
                "id": o.code,
                "api": o.api_name,
                "name": o.display_name,
                "group": o.obj_group,
                "description": o.description or "",
                "rowAccess": row_access,
                "properties": _visible_props(o),
            }
        )
    return out, {o.code: o for o in rows}


async def _metrics(session, user) -> list[dict]:
    rows = list(
        await session.scalars(
            select(Metric).where(Metric.status == MetricStatus.published).order_by(Metric.seq, Metric.id)
        )
    )
    ready: dict[str, dict] = {}
    codes = [m.code for m in rows]
    for i in range(0, len(codes), READINESS_BATCH_MAX):
        batch = await metrics_readiness(session, user, codes[i : i + READINESS_BATCH_MAX])
        ready.update({item["id"]: item for item in batch["items"]})
    return [
        {
            "id": m.code,
            "api": m.api_name,
            "name": m.display_name,
            "group": m.metric_group,
            "unit": m.unit,
            "version": m.version,
            "dims": list(m.dims or []),
            "trialReady": bool(ready.get(m.code, {}).get("ready")),
            "issues": list(ready.get(m.code, {}).get("issues") or []),
        }
        for m in rows
    ]


async def _functions(session) -> list[dict]:
    fns = list(
        await session.scalars(select(Function).where(Function.status == "published").order_by(Function.name))
    )
    params: dict[int, list[FnParam]] = {}
    if fns:
        for p in await session.scalars(
            select(FnParam)
            .where(FnParam.function_id.in_([f.id for f in fns]))
            .order_by(FnParam.function_id, FnParam.direction, FnParam.seq)
        ):
            params.setdefault(p.function_id, []).append(p)

    def shape(p: FnParam) -> dict:
        return {
            "name": p.name,
            "type": p.type,
            **({"objectType": p.obj_type} if p.obj_type else {}),
            "desc": p.desc,
        }

    return [
        {
            "id": f.code,
            "name": f.name,
            "title": f.title,
            "runtime": f.runtime,
            "description": f.description,
            "inputs": [shape(p) for p in params.get(f.id, []) if p.direction == "in"],
            "outputs": [shape(p) for p in params.get(f.id, []) if p.direction == "out"],
        }
        for f in fns
    ]


def _machine(code: str, key_context) -> dict:
    prefix = api_prefix("ontology")
    out: dict[str, Any] = {
        "execute": {
            "method": "POST",
            "path": f"{prefix}/machine/actions/{code}:execute",
            "scope": "actions:execute",
            "idempotencyHeader": "Idempotency-Key",
        },
        "run": {
            "method": "GET",
            "path": f"{prefix}/machine/action-runs/{{runId}}",
            "scope": "actions:execute",
        },
    }
    if key_context is not None:  # API 密钥调用：提示这把密钥能不能直接执行
        allow = key_context.action_types
        out["keyAllowed"] = "actions:execute" in key_context.scopes and (allow is None or code in allow)
    return out


async def _actions(session, user, objects, access, key_context) -> list[dict]:
    rows = [
        a
        for a in await session.scalars(
            select(ActionType)
            .where(
                ActionType.status.in_([TypeStatus.published, TypeStatus.changing]),
                ActionType.retired_release_id.is_(None),
            )
            .order_by(ActionType.code)
        )
        if a.target_kind or a.target_api
    ]
    stored = await action_policy.version_policies(session, [(a.id, a.version) for a in rows if a.version])
    out = []
    for a in rows:
        obj = objects.get(a.target_kind or "")
        out.append(
            {
                "id": a.code,
                "api": a.api,
                "name": a.name,
                "group": a.grp,
                "version": a.version,
                "status": a.status.value,
                "object": a.target_kind,
                "objectApi": a.target_api or (obj.api_name if obj else ""),
                "rowAccess": await _row_access(user, a.target_kind, access),
                "params": [
                    {"api": p.api, "name": p.name, "type": p.type, "required": p.required} for p in a.params
                ],
                "external": a.system.name if a.system is not None else None,
                "policy": action_policy.stored(stored.get(a.id)).dump(),
                "machine": _machine(a.code, key_context),
            }
        )
    return out


async def _rules(session, user, access) -> list[dict]:
    rows = list(
        await session.scalars(
            select(Rule)
            .where(Rule.status.in_([RuleStatus.published, RuleStatus.changing]), Rule.version.is_not(None))
            .order_by(Rule.code)
        )
    )
    action_codes: dict[int, str] = {}
    ids = {r.action_type_id for r in rows if r.action_type_id}
    if ids:
        action_codes = dict(
            (
                await session.execute(select(ActionType.id, ActionType.code).where(ActionType.id.in_(ids)))
            ).all()
        )
    return [
        {
            "id": r.code,
            "name": r.name,
            "desc": r.desc,
            "version": r.version,
            "status": r.status.value,
            "object": r.obj_kind,
            "rowAccess": await _row_access(user, r.obj_kind, access),
            "level": getattr(r.level, "value", r.level),
            "output": r.output.value,
            "trigger": (r.trigger or {}).get("kind"),
            "actionType": action_codes.get(r.action_type_id) if r.action_type_id else None,
        }
        for r in rows
    ]


async def capabilities(
    session,
    user: CurrentUser,
    request: Request | None = None,
    *,
    ver: str | None = None,
    readable_only: bool = False,
) -> dict[str, Any]:
    if ver:
        raise AppError(
            "能力目录只提供当前版本", code="VALIDATION_FAILED", status_code=422, details={"ver": ver}
        )
    allowed = {name: _can_view(user, page) for name, page in SECTIONS}
    if not any(allowed.values()):
        raise Forbidden("没有任何能力目录分节的查看权限", details={"pages": [p for _, p in SECTIONS]})
    key_context = getattr(request.state, "api_key", None) if request is not None else None
    access: dict[str, str] = {}
    out: dict[str, Any] = {"notes": NOTES, "omitted": []}
    objects: dict[str, ObjectType] = {}
    if allowed["objects"]:
        out["objects"], objects = await _objects(session, user, access, readable_only)
    if allowed["metrics"]:
        out["metrics"] = await _metrics(session, user)
    if allowed["functions"]:
        out["functions"] = await _functions(session)
    if allowed["actions"]:
        out["actions"] = await _actions(session, user, objects, access, key_context)
    if allowed["rules"]:
        out["rules"] = await _rules(session, user, access)
    out["omitted"] = [
        {"section": name, "page": page, "reason": "NO_PERMISSION"}
        for name, page in SECTIONS
        if not allowed[name]
    ]
    return out
