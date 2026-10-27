"""问数 MCP 服务：问数专属工具 + 平台工具导出（`GET /machine/tools?format=mcp`）的同一份清单。

- 入口：`POST /api/qa/v1/mcp`，MCP Streamable HTTP 的无状态子集（JSON-RPC 2.0，单请求单响应，
  `application/json`；不开 SSE 通道、不发服务端通知）。方法：`initialize` / `notifications/initialized` /
  `ping` / `tools/list` / `tools/call`。协议形状按 MCP 规范（2025-06-18）自写，不引第三方 SDK
  （官方 `mcp` Python SDK 是 MIT，可替换；pyproject 不归问数管，见 handoff）。
- 工具：
  - 问数专属（本文件）：`qa_ask`（objects:read）、`metric_query`（meta:read）。
  - `object_query`（objects:read）：转发到平台 `POST /machine/objects:query`，不另写对象查询。
  - 动作 / 事件 / 函数：清单就是 `machine.tools.export(..., "mcp")`（能力目录 + 密钥 scope / 白名单 +
    执行策略过滤，归 palantir），调用按导出的 binding 转发到平台机器接口（同一调用身份的请求头，
    动作带 Idempotency-Key），鉴权 / 策略 / 配额都由目标接口自己执行；问数不复制这些规则。
- 鉴权：入口 `require_api_scope("meta:read")`（同工具清单接口）；每个工具再按自己的 scope 校验，
  没有 → JSON-RPC 错误 -32001，`tools/list` 也只列有权的。qa_ask 回答里的告警引用需要 dynamic:read
  （没有就去掉）；qa_ask 落成动作建议时只回建议卡（dry-run 明细另需 actions:dry-run 且动作在密钥白名单内），
  从不暂存或执行——要执行就调导出的动作工具。人工会话（无密钥）按各工具底层接口原有的功能权限 / 行级范围校验。
- tools/list 里的取值范围（objectType / metric 枚举）取自能力目录（`static.capabilities`），不自建。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.common.auth import CurrentUserDep, check_func
from app.core.errors import AppError
from app.db.session import SessionDep
from app.modules.admin.api_keys import enforce_api_allowlist, require_api_scope
from app.modules.admin.model_settings import ModelRuntime
from app.modules.qa import chain
from app.modules.qa.llm import LLMClient, configured_runtime, get_llm

# 转发到平台机器接口时沿用的调用身份请求头
FORWARD_HEADERS = ("authorization", "cookie", "x-ontology-id", "x-demo-as")

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "rengu-qa", "title": "问数", "version": "1.1"}
# JSON-RPC 错误码：标准码 + 本服务自定义（-32001 权限不足）
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
FORBIDDEN = -32001

mcp = APIRouter()


async def mcp_llm(
    runtime: Annotated[ModelRuntime | None, Depends(configured_runtime)] = None,
) -> LLMClient | AppError:
    """问数大模型；没配 / 停用时不让整个 MCP 入口 503，只让 qa_ask 报错。测试覆盖这个依赖。"""
    try:
        return get_llm(runtime)
    except AppError as e:
        return e


@dataclass
class Ctx:
    session: Any
    user: Any
    request: Request
    llm: LLMClient | AppError
    opener: Callable


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    scope: str
    schema: dict[str, Any]
    run: Callable[[Ctx, dict[str, Any]], Awaitable[Any]]

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        }


def _key_scopes(request: Request) -> tuple[str, ...] | None:
    ctx = getattr(request.state, "api_key", None)
    return None if ctx is None else tuple(ctx.scopes)


# ---------------------------------------------------------------- 工具实现（都走已有服务，进程内调用）


async def _qa_ask(c: Ctx, args: dict[str, Any]) -> dict[str, Any]:
    question = str(args.get("question") or "").strip()
    if not question or len(question) > 500:
        raise _ParamError("question 必填，最多 500 字")
    if _key_scopes(c.request) is None:  # 人工会话：同工作台
        check_func(c.user, "qa-workbench", "view")
    else:  # 密钥：objects:read 带的对象读取页面权限
        from app.modules.qa.object_source import check_object_read

        check_object_read(c.user)
    if isinstance(c.llm, AppError):
        raise c.llm
    from sqlalchemy.exc import DBAPIError

    from app.modules.qa.router import QaSqlFailed

    try:
        async with c.opener(c.user.ontology_id) as conn:
            kind, snap, rows, elapsed, _sql = await chain.answer(c.session, conn, c.llm, c.user, question)
    except DBAPIError as e:  # 同工作台：生成的 SQL 执行出错 → 换个问法（SQL 本身不出接口）
        raise QaSqlFailed(details={"reason": type(e.orig).__name__ if e.orig else type(e).__name__}) from e
    out = dict(snap)
    answer = out.get("answer")
    scopes = _key_scopes(c.request)
    if isinstance(answer, dict) and answer.get("card") == "action":
        answer = out["answer"] = _action_for_machine(answer, scopes, c.request)
    if isinstance(answer, dict) and scopes is not None and "dynamic:read" not in scopes:
        # 告警 / 规则命中属于动态层：密钥要有 dynamic:read（链路本身也按 alerts 页权限取，这里兜底）
        out["answer"] = {k: v for k, v in answer.items() if k != "alerts"}
    return {"kind": kind, "rowCount": rows, "elapsedMs": elapsed, **out}


def _action_for_machine(answer: dict[str, Any], scopes: tuple[str, ...] | None, request: Request) -> dict:
    """机器调用方的动作建议：没有 actions:dry-run（或动作不在密钥白名单）→ 去掉校验明细；永不带执行入口。"""
    out = dict(answer)
    action = dict(out.get("action") or {})
    allowed = scopes is None or "actions:dry-run" in scopes
    if allowed:
        try:
            enforce_api_allowlist(request, action_type=action.get("type"))
        except AppError:
            allowed = False
    if not allowed:
        action.pop("dryRun", None)
        action["dryRunHidden"] = "需要 actions:dry-run 权限"
    action["canStage"] = False  # MCP 只建议；暂存 / 确认只在问数工作台或动作中心由人完成
    out["action"] = action
    return out


async def _forward(c: Ctx, method: str, path: str, body: Any, extra: dict[str, str] | None = None) -> Any:
    """进程内转发到平台机器接口（同一应用、同一调用身份）：目标接口自己做鉴权 / 策略 / 配额 / 审计。"""
    import httpx

    headers = {k: v for k, v in c.request.headers.items() if k.lower() in FORWARD_HEADERS}
    transport = httpx.ASGITransport(app=c.request.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mcp.internal", timeout=60) as cli:
        res = await cli.request(method, path, json=body, headers={**headers, **(extra or {})})
    try:
        data = res.json()
    except ValueError:
        data = {"text": res.text}
    if res.status_code >= 400:
        err = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else data
        raise _Remote(res.status_code, err)
    return data


class _Remote(Exception):
    def __init__(self, status: int, body: Any):
        super().__init__(status)
        self.status, self.body = status, body


async def _object_query(c: Ctx, args: dict[str, Any]) -> Any:
    from app.core.config import api_prefix

    if not str(args.get("objectType") or "").strip():
        raise _ParamError("objectType 必填")
    body = {k: v for k, v in args.items() if k in ("objectType", "kw", "filters", "sort", "page", "size")}
    out = await _forward(c, "POST", f"{api_prefix('ontology')}/machine/objects:query", body)
    if isinstance(out, dict):
        out.pop("facets", None)
    return out


async def _machine_tools(c: Ctx) -> dict[str, Any]:
    """平台工具导出（同 `GET /machine/tools?format=mcp`）；读不到（无权 / 出错）就当没有。"""
    from app.modules.machine import tools as machine

    try:
        async with c.session.begin_nested():
            return await machine.export(c.session, c.user, c.request, "mcp")
    except Exception:
        return {"tools": [], "bindings": {}}


def _idempotency_key(c: Ctx, name: str, args: dict[str, Any], rid: Any) -> str:
    import hashlib

    who = getattr(getattr(c.request.state, "api_key", None), "key_id", None) or getattr(c.user, "id", "")
    raw = json.dumps([str(who), name, args, rid], ensure_ascii=False, sort_keys=True, default=str)
    return "mcp-" + hashlib.sha256(raw.encode()).hexdigest()[:40]


async def _metric_query(c: Ctx, args: dict[str, Any]) -> Any:
    from app.modules.static.metric_trial import evaluate_metric

    code = str(args.get("metric") or "").strip()
    if not code or len(code) > 64:
        raise _ParamError("metric 必填（指标编号）")
    period = str(args.get("period") or "day")
    if period not in ("day", "week", "month"):
        raise _ParamError("period 只能是 day / week / month")
    days = _int(args, "days", 30, 1, 93)
    return await evaluate_metric(c.session, c.user, code, days=days, period=period)


class _ParamError(Exception):
    pass


def _int(args: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise _ParamError(f"{key} 须为 {lo}–{hi} 的整数")
    return v


_STR = {"type": "string"}
TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "qa_ask",
            "问数",
            "用自然语言问当前工作空间的数据，返回回答卡（文字 / 结果表 / 引用）。只读；不建会话。"
            "问题是要“做一件事”时只回动作建议，不会执行。",
            "objects:read",
            {
                "type": "object",
                "properties": {"question": {**_STR, "maxLength": 500, "description": "问题"}},
                "required": ["question"],
                "additionalProperties": False,
            },
            _qa_ask,
        ),
        Tool(
            "object_query",
            "对象查询",
            "按对象类型 + 关键词 + 属性筛选 + 排序分页取对象行"
            "（平台 POST /machine/objects:query，按调用身份的行级范围）。",
            "objects:read",
            {
                "type": "object",
                "properties": {
                    "objectType": {
                        **_STR,
                        "description": "对象类型编码（GET /meta/capabilities 的 objects[].id）",
                    },
                    "kw": {**_STR, "maxLength": 100, "description": "关键词"},
                    "filters": {
                        "type": "object",
                        "description": "属性 api 名 → 取值列表，或 {min,max}（数值 / 时间闭区间）",
                    },
                    "sort": {**_STR, "description": "属性 api 名，前缀 - 倒序"},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "size": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
                },
                "required": ["objectType"],
                "additionalProperties": False,
            },
            _object_query,
        ),
        Tool(
            "metric_query",
            "指标查询",
            "按已发布指标定义计算最近 N 天的指标值（按日 / 周 / 月分组；当前身份可见数据）。",
            "meta:read",
            {
                "type": "object",
                "properties": {
                    "metric": {
                        **_STR,
                        "description": "指标编号（GET /meta/capabilities 的 metrics[].id，trialReady）",
                    },
                    "days": {"type": "integer", "minimum": 1, "maximum": 93, "default": 30},
                    "period": {"type": "string", "enum": ["day", "week", "month"], "default": "day"},
                },
                "required": ["metric"],
                "additionalProperties": False,
            },
            _metric_query,
        ),
    )
}


async def _with_catalog(c: Ctx, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """tools/list 的取值范围来自能力目录：objectType 只列调用者读得到行的已发布对象，metric 只列可试算指标。

    目录读不到（无权 / 出错）就按原样列，调用时各工具照常校验。
    """
    from app.modules.static.capabilities import capabilities

    try:
        async with c.session.begin_nested():
            cat = await capabilities(c.session, c.user, c.request, readable_only=True)
    except Exception:
        return tools
    enums = {
        ("object_query", "objectType"): [o["id"] for o in cat.get("objects") or []]
        if "objects" in cat
        else None,
        ("metric_query", "metric"): [m["id"] for m in cat.get("metrics") or [] if m.get("trialReady")]
        if "metrics" in cat
        else None,
    }
    for t in tools:
        for (name, prop), values in enums.items():
            if t["name"] == name and values is not None:
                schema = json.loads(json.dumps(t["inputSchema"]))
                schema["properties"][prop]["enum"] = values
                t["inputSchema"] = schema
    return tools


# ---------------------------------------------------------------- JSON-RPC


def _ok(rid: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    e: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": e}


def _tool_result(value: Any, is_error: bool = False) -> dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False, default=str)
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if isinstance(value, dict) and not is_error:
        out["structuredContent"] = json.loads(text)
    return out


async def call_tool(c: Ctx, name: str, args: dict[str, Any], rid: Any = None) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        return await _call_machine(c, name, args, rid)
    scopes = _key_scopes(c.request)
    if scopes is not None and tool.scope not in scopes:
        return _err(rid, FORBIDDEN, "API 密钥没有此工具的权限", {"required": tool.scope})
    if not isinstance(args, dict):
        return _err(rid, INVALID_PARAMS, "arguments 须为对象")
    try:
        async with c.session.begin_nested():  # 工具失败不弄坏请求事务（审计等照常提交）
            value = await tool.run(c, args)
    except _ParamError as e:
        return _err(rid, INVALID_PARAMS, str(e))
    except AppError as e:  # 业务错误按 MCP 约定放进工具结果（isError），让模型看得到原因
        return _ok(rid, _tool_result({"code": e.code, "message": e.message, "details": e.details}, True))
    except _Remote as e:
        return _ok(rid, _tool_result({"status": e.status, **_as_dict(e.body)}, True))
    return _ok(rid, _tool_result(value))


def _as_dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {"detail": v}


async def _call_machine(c: Ctx, name: str, args: dict[str, Any], rid: Any) -> dict[str, Any]:
    """平台导出的动作 / 事件 / 函数工具：清单现取（调用时仍须在导出里），按 binding 转发。"""
    binding = (await _machine_tools(c))["bindings"].get(name)
    if binding is None:
        return _err(rid, INVALID_PARAMS, f"未知工具：{name}")
    if not isinstance(args, dict):
        return _err(rid, INVALID_PARAMS, "arguments 须为对象")
    extra = {h: _idempotency_key(c, name, args, rid) for h in binding.get("headers") or []}
    try:
        value = await _forward(c, binding["method"], binding["path"], args, extra)
    except _Remote as e:
        return _ok(rid, _tool_result({"status": e.status, **_as_dict(e.body)}, True))
    return _ok(rid, _tool_result(value))


async def handle(c: Ctx, msg: Any) -> dict[str, Any] | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _err(
            msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "不是 JSON-RPC 2.0 请求"
        )
    method, rid, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:  # 通知：不回
        return None
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        return _ok(
            rid,
            {
                "protocolVersion": asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "问数 qa_ask / metric_query / object_query，另含平台导出的动作 / 函数工具",
            },
        )
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        scopes = _key_scopes(c.request)
        tools = [t.listing() for t in TOOLS.values() if scopes is None or t.scope in scopes]
        tools = await _with_catalog(c, tools)
        taken = {t["name"] for t in tools}
        exported = (await _machine_tools(c))["tools"]
        tools += [t for t in exported if t["name"] not in taken]
        return _ok(rid, {"tools": tools})
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _err(rid, INVALID_PARAMS, "缺少工具名 name")
        return await call_tool(c, params["name"], params.get("arguments") or {}, rid)
    return _err(rid, METHOD_NOT_FOUND, f"不支持的方法：{method}")


@mcp.post(
    "/mcp",
    dependencies=[Depends(require_api_scope("meta:read"))],
    summary="M1 问数 MCP（问数工具 + 平台工具导出；JSON-RPC 2.0，无状态）",
)
async def mcp_endpoint(
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
    llm: Annotated[LLMClient | AppError, Depends(mcp_llm)],
    opener: Annotated[Callable, Depends(chain.qa_conn_opener)],
):
    try:
        payload = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse(_err(None, PARSE_ERROR, "JSON 解析失败"))
    c = Ctx(session, user, request, llm, opener)
    if isinstance(payload, list):  # 批量（2025-03-26 兼容）
        if not payload:
            return JSONResponse(_err(None, INVALID_REQUEST, "空批量"))
        out = [r for r in [await handle(c, m) for m in payload] if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    res = await handle(c, payload)
    return JSONResponse(res) if res is not None else Response(status_code=202)


@mcp.get("/mcp", include_in_schema=False)
async def mcp_no_sse():
    """无状态实现不开服务端推送通道（规范允许 405）。"""
    return Response(status_code=405, headers={"Allow": "POST"})
