"""AI 工具清单：静态能力目录（static.capabilities，GET /meta/capabilities 同一服务）的薄转换层。

动作 / 函数取自能力目录（已发布、按页面权限分节、密钥 keyAllowed 已含 scope + 动作白名单）；
能力目录没有事件类型一节，事件类型在这里补读（已发布、外部 / 同步来源、按密钥事件白名单）。
再按动作执行策略（suggest 不导出、callers 不含调用方不导出）过滤。只读，不写任何东西。
格式：mcp（tools/list 的 {name, description, inputSchema}）/ openai（{"type":"function","function":{…}}）。
"""

import re
from typing import Any

from fastapi import Request
from sqlalchemy import select

from app.common.auth import CurrentUser
from app.core.config import api_prefix
from app.modules.action.checker import literal_enum_choices
from app.modules.action.models import TypeStatus
from app.modules.action.policy import caller_of
from app.modules.admin.api_keys import ApiKeyContext
from app.modules.event.models import EventSource, EventType
from app.modules.static.capabilities import capabilities

# 只导出外部可写入的事件来源；规则 / 动作产生的事件由平台自己发，不给 AI 当工具
INGESTIBLE = (EventSource.external, EventSource.sync)
SCALAR = {
    "字符串": {"type": "string"},
    "文本": {"type": "string"},
    "枚举": {"type": "string"},
    "整数": {"type": "integer"},
    "金额": {"type": "number"},
    "百分比": {"type": "number"},
    "小数": {"type": "number"},
    "数值": {"type": "number"},
    "布尔": {"type": "boolean"},
    "日期": {"type": "string", "format": "date"},
    "日期时间": {"type": "string", "format": "date-time"},
}
PY_TYPES = {
    "int": "integer",
    "float": "number",
    "Decimal": "number",
    "decimal.Decimal": "number",
    "str": "string",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def type_schema(kind: str, rule: str = "") -> dict:
    """动作参数 / 事件载荷的类型 → JSON Schema（与 checker.param_valid 的取值形状一致）。"""
    kind = (kind or "").strip()
    if kind in SCALAR:
        out = dict(SCALAR[kind])
        if kind == "枚举" and (choices := literal_enum_choices(rule)):
            out["enum"] = choices
        return out
    if kind == "枚举列表":
        items: dict = {"type": "string"}
        if choices := literal_enum_choices(rule):
            items["enum"] = choices
        return {"type": "array", "items": items}
    if kind.startswith("对象引用列表"):
        return {"type": "array", "items": {"type": "string"}, "description": f"{_ref(kind)} 编号列表"}
    if kind.startswith("对象引用"):
        return {"type": "string", "description": f"{_ref(kind)} 编号"}
    return {"description": f"类型：{kind}"}


def _ref(kind: str) -> str:
    return kind.split("·", 1)[1].strip() if "·" in kind else "对象"


def py_schema(annotation: str) -> dict:
    base = re.sub(r"\[.*\]$", "", (annotation or "").strip())
    if base.startswith("list"):
        return {"type": "array"}
    if base.startswith("dict"):
        return {"type": "object"}
    return {"type": PY_TYPES[base]} if base in PY_TYPES else {}


def _describe(schema: dict, *parts: str) -> dict:
    text = "；".join(p for p in [schema.get("description", ""), *parts] if p)
    return {**schema, **({"description": text} if text else {})}


def _object(props: dict[str, dict], required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def tool_name(prefix: str, raw: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]", "_", f"{prefix}_{raw}")[:64]
    name, n = base, 2
    while name in taken:
        suffix = f"_{n}"
        name, n = base[: 64 - len(suffix)] + suffix, n + 1
    taken.add(name)
    return name


def _allowed(allow: tuple[str, ...] | None, *ids: str) -> bool:
    return allow is None or any(i in allow for i in ids if i)


def _has(key: ApiKeyContext | None, scope: str) -> bool:
    return key is None or scope in key.scopes


def _can(user: CurrentUser, key: ApiKeyContext | None, scope: str, page: str) -> bool:
    return _has(key, scope) and user.func.get(page, "none") in ("view", "manage")


def action_tools(
    actions: list[dict], user: CurrentUser, key: ApiKeyContext | None, taken: set[str]
) -> list[dict]:
    """能力目录 actions 节 → 工具。密钥：machine.keyAllowed（scope + 动作白名单，静态目录已算）。"""
    caller = caller_of("手动", user)
    out = []
    for a in actions:
        policy = a.get("policy") or {"mode": "confirm"}
        callers = policy.get("callers")
        if key is not None and not a.get("machine", {}).get("keyAllowed"):
            continue
        if policy.get("mode") == "suggest" or (callers is not None and caller not in callers):
            continue  # 只给建议（执行会 409）/ 策略不允许该调用方（执行会 403）：不导出
        params = {
            p["api"]: _describe(type_schema(p["type"]), p["name"])
            for p in a["params"]
            if not p["api"].startswith("_")
        }
        required = [p["api"] for p in a["params"] if p["required"] and p["api"] in params]
        props: dict[str, dict] = {
            "objId": {"type": "string", "description": f"目标对象编号（{a.get('object') or '对象'}）"},
            "params": _object(params, required),
        }
        if a.get("object"):
            props["objKind"] = {
                "type": "string",
                "enum": [a["object"]],
                "description": "目标对象类型（固定）",
            }
        text = f"{a['name']}。" + (
            "执行策略 auto：上限内自动执行，超限或需审批转人工确认"
            if policy.get("mode") == "auto"
            else "执行策略 confirm：只生成待人工确认的执行记录"
        )
        execute = a["machine"]["execute"]
        out.append(
            {
                "name": tool_name("action", a.get("api") or a["id"], taken),
                "description": text,
                "schema": _object(props, ["objId", "params", *(["objKind"] if a.get("object") else [])]),
                "binding": {
                    "kind": "action",
                    "id": a["id"],
                    "version": a.get("version"),
                    "method": execute["method"],
                    "path": execute["path"],
                    "headers": [execute["idempotencyHeader"]],
                    "scope": execute["scope"],
                    "policyMode": policy.get("mode"),
                },
            }
        )
    return out


async def event_tools(session, key: ApiKeyContext | None, taken: set[str]) -> list[dict]:
    rows = list(
        await session.scalars(
            select(EventType)
            .where(
                EventType.status == TypeStatus.published,
                EventType.retired_release_id.is_(None),
                EventType.source.in_(INGESTIBLE),
            )
            .order_by(EventType.code)
        )
    )
    out = []
    for e in rows:
        if not _allowed(key.event_types if key else None, e.code, e.api):
            continue
        payload = {f.api: _describe(type_schema(f.type), f.name, f.description) for f in e.payload}
        kinds = [k for k in dict.fromkeys([e.object_api, e.obj_kind]) if k]
        props = {
            "eventId": {"type": "string", "minLength": 1, "description": "调用方生成的全局唯一事件编号"},
            "eventType": {"type": "string", "enum": [e.api]},
            "occurredAt": {"type": "string", "format": "date-time", "description": "带时区"},
            "source": {"type": "string", "minLength": 1},
            "objectType": {"type": "string", **({"enum": kinds} if kinds else {})},
            "objectId": {"type": "string", "minLength": 1},
            "payload": _object(payload, [f.api for f in e.payload if f.required]),
            "dedupKey": {"type": "string"},
            "traceId": {"type": "string"},
        }
        out.append(
            {
                "name": tool_name("event", e.api or e.code, taken),
                "description": f"{e.name}。{e.description}".rstrip("。") + "。",
                "schema": _object(
                    props,
                    ["eventId", "eventType", "occurredAt", "source", "objectType", "objectId", "payload"],
                ),
                "binding": {
                    "kind": "event",
                    "id": e.code,
                    "version": e.version,
                    "method": "POST",
                    "path": "/internal/v1/events",
                    "scope": "events:ingest",
                },
            }
        )
    return out


def function_tools(functions: list[dict], taken: set[str]) -> list[dict]:
    """能力目录 functions 节（已发布）→ 工具。没有按函数的密钥白名单（只看 functions:invoke）。"""
    out = []
    for fn in functions:
        inputs = {p["name"]: _describe(py_schema(p["type"]), p.get("desc", "")) for p in fn["inputs"]}
        out.append(
            {
                "name": tool_name("fn", fn["name"], taken),
                "description": f"{fn['title']}。{fn['description']}".rstrip("。") + "。",
                "schema": _object({"input": _object(inputs, list(inputs))}, ["input"]),
                "binding": {
                    "kind": "function",
                    "id": fn["id"],
                    "method": "POST",
                    "path": f"{api_prefix('ontology')}/machine/functions/{fn['id']}:invoke",
                    "scope": "functions:invoke",
                },
            }
        )
    return out


async def export(session, user: CurrentUser, request: Request, fmt: str) -> dict:
    """静态能力目录（GET /meta/capabilities 的同一服务）→ 工具；事件类型目录里没有，这里补读。"""
    key: ApiKeyContext | None = getattr(request.state, "api_key", None)
    catalog = await capabilities(session, user, request)
    taken: set[str] = set()
    tools: list[dict] = []
    if "actions" in catalog and user.func.get("action-center") == "manage" and _has(key, "actions:execute"):
        tools += action_tools(catalog["actions"], user, key, taken)
    if _can(user, key, "events:ingest", "event-types"):
        tools += await event_tools(session, key, taken)
    if "functions" in catalog and _has(key, "functions:invoke"):
        tools += function_tools(catalog["functions"], taken)
    return {
        "format": fmt,
        "tools": [render(t, fmt) for t in tools],
        "bindings": {t["name"]: t["binding"] for t in tools},
    }


def render(tool: dict[str, Any], fmt: str) -> dict:
    if fmt == "openai":
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["schema"],
            },
        }
    return {"name": tool["name"], "description": tool["description"], "inputSchema": tool["schema"]}
