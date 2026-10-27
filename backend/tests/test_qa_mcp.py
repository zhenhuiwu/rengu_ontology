"""问数 MCP（mcp_tools.py）：JSON-RPC、按工具 scope 鉴权、能力目录取值范围、平台工具导出合并 / 转发。"""

import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select, text

from app.db.session import get_session
from app.main import app
from app.modules.qa import chain, mcp_tools
from app.modules.qa.llm import FakeLLM
from app.modules.qa.models import QaTurn
from tests.qa_access_support import seed_qa_access

URL = "/api/qa/v1/mcp"
SQL = "```sql\nselect count(*) as \"门店数\" from store s where s.big_region = '华东';\n```"
ADMIN = {"X-Demo-As": "U010245"}
QA_TOOLS = {"qa_ask", "object_query", "metric_query"}


@pytest.fixture
async def qa(users, client, session):
    await seed_qa_access(users)
    await users.execute(
        text("UPDATE ontology_member SET role='editor' WHERE ontology_id=1 AND role='viewer'")
    )
    await users.flush()

    @asynccontextmanager
    async def _open(_oid):
        yield await session.connection()

    async def _plain():
        yield session

    app.dependency_overrides[get_session] = _plain
    app.dependency_overrides[chain.qa_conn_opener] = lambda: _open
    yield client
    for k in (chain.qa_conn_opener, mcp_tools.mcp_llm):
        app.dependency_overrides.pop(k, None)


def use_llm(reply):
    fake = FakeLLM(reply)
    app.dependency_overrides[mcp_tools.mcp_llm] = lambda: fake
    return fake


async def rpc(c, method, params=None, rid=1, headers=None):
    body = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": method,
        **({"params": params} if params is not None else {}),
    }
    r = await c.post(URL, json=body, headers=headers or {})
    assert r.status_code == 200, r.text
    return r.json()


async def call(c, name, args=None, headers=None):
    return await rpc(c, "tools/call", {"name": name, "arguments": args or {}}, headers=headers)


def payload(res):
    out = res["result"]
    assert out["content"][0]["type"] == "text"
    return out, json.loads(out["content"][0]["text"])


async def key(c, *scopes):
    body = {
        "name": "k-mcp-" + "-".join(s.split(":")[0] for s in scopes),
        "env": "测试",
        "scopes": list(scopes),
        "binding": {"kind": "用户", "userId": "U010245"},
    }
    r = await c.post("/api/admin/v1/api-keys", json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['secret']}"}


async def test_protocol_shape(qa):
    init = await rpc(qa, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    res = init["result"]
    assert res["protocolVersion"] == "2025-03-26" and res["capabilities"]["tools"] == {"listChanged": False}
    assert res["serverInfo"]["name"] == "rengu-qa"
    assert (await rpc(qa, "initialize", {"protocolVersion": "1999-01-01"}))["result"]["protocolVersion"] == (
        mcp_tools.PROTOCOL_VERSION
    )
    assert (await rpc(qa, "ping"))["result"] == {}
    assert (await rpc(qa, "nope"))["error"]["code"] == mcp_tools.METHOD_NOT_FOUND
    note = await qa.post(URL, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert note.status_code == 202 and note.content == b""
    bad = await qa.post(URL, content=b"{", headers={"Content-Type": "application/json"})
    assert bad.json()["error"]["code"] == mcp_tools.PARSE_ERROR
    assert await qa.post(URL, json={"id": 1})
    assert (await qa.post(URL, json={"id": 1})).json()["error"]["code"] == mcp_tools.INVALID_REQUEST
    batch = await qa.post(
        URL,
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        ],
    )
    assert [x["id"] for x in batch.json()] == [1]
    assert (await qa.get(URL)).status_code == 405
    unknown = await call(qa, "drop_table")
    assert unknown["error"]["code"] == mcp_tools.INVALID_PARAMS


async def test_tools_list_uses_capability_catalog(qa, monkeypatch):
    from app.modules.static import capabilities as caps

    async def fake_caps(_s, _u, _r=None, **kw):
        assert kw == {"readable_only": True}
        return {
            "objects": [{"id": "store"}],
            "metrics": [{"id": "m_ok", "trialReady": True}, {"id": "m_no", "trialReady": False}],
            "omitted": [],
        }

    monkeypatch.setattr(caps, "capabilities", fake_caps)
    tools = {t["name"]: t for t in (await rpc(qa, "tools/list"))["result"]["tools"]}
    assert set(tools) >= QA_TOOLS
    assert all(tools[n]["annotations"]["readOnlyHint"] for n in QA_TOOLS)
    assert tools["object_query"]["inputSchema"]["properties"]["objectType"]["enum"] == ["store"]
    assert tools["metric_query"]["inputSchema"]["properties"]["metric"]["enum"] == ["m_ok"]
    # 静态定义没被改写
    assert "enum" not in mcp_tools.TOOLS["object_query"].schema["properties"]["objectType"]

    async def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(caps, "capabilities", boom)
    tools = {t["name"]: t for t in (await rpc(qa, "tools/list"))["result"]["tools"]}
    assert "enum" not in tools["object_query"]["inputSchema"]["properties"]["objectType"]


async def test_object_query_forwards_to_machine_endpoint(qa):
    out, d = payload(await call(qa, "object_query", {"objectType": "store", "size": 5}))
    assert out["isError"] is False and out["structuredContent"]["total"] == d["total"] == 95  # 华东行级范围
    assert len(d["items"]) == 5 and d["objectType"] == "store" and "facets" not in d
    assert (await call(qa, "object_query", {}))["error"]["code"] == mcp_tools.INVALID_PARAMS
    out, e = payload(await call(qa, "object_query", {"objectType": "store", "size": 0}))
    assert out["isError"] is True and e["status"] == 422  # 平台接口自己的校验
    out, e = payload(await call(qa, "object_query", {"objectType": "no_such_type"}))
    assert out["isError"] is True and e["status"] >= 400 and e["code"]


async def test_machine_tools_are_listed_and_forwarded(qa, monkeypatch):
    from app.modules.machine import tools as machine

    async def fake_export(_s, _u, _r, fmt):
        assert fmt == "mcp"
        return {
            "format": fmt,
            "tools": [
                {"name": "fn_calc", "description": "d", "inputSchema": {"type": "object"}},
                {"name": "qa_ask", "description": "撞名", "inputSchema": {"type": "object"}},
            ],
            "bindings": {
                "fn_calc": {
                    "kind": "function",
                    "method": "POST",
                    "path": "/x/fn",
                    "scope": "functions:invoke",
                },
                "action_A": {
                    "kind": "action",
                    "method": "POST",
                    "path": "/x/act",
                    "headers": ["Idempotency-Key"],
                },
            },
        }

    seen = []

    async def fake_forward(_c, method, path, body, extra=None):
        seen.append((method, path, body, extra))
        if path == "/x/act":
            raise mcp_tools._Remote(409, {"code": "ACTION_POLICY_SUGGEST_ONLY", "message": "m"})
        return {"ok": True}

    monkeypatch.setattr(machine, "export", fake_export)
    monkeypatch.setattr(mcp_tools, "_forward", fake_forward)
    tools = [t["name"] for t in (await rpc(qa, "tools/list"))["result"]["tools"]]
    assert tools.count("qa_ask") == 1 and "fn_calc" in tools  # 问数工具优先，导出工具追加
    out, d = payload(await call(qa, "fn_calc", {"input": {"price": 2}}))
    assert out["isError"] is False and d == {"ok": True}
    assert seen[-1] == ("POST", "/x/fn", {"input": {"price": 2}}, {})
    out, e = payload(await call(qa, "action_A", {"objId": "S1", "params": {}}))
    assert out["isError"] is True and e["status"] == 409 and e["code"] == "ACTION_POLICY_SUGGEST_ONLY"
    k1 = seen[-1][3]["Idempotency-Key"]
    await call(qa, "action_A", {"objId": "S1", "params": {}})
    assert seen[-1][3]["Idempotency-Key"] == k1  # 同一请求 id 重试 → 同一幂等键
    await rpc(qa, "tools/call", {"name": "action_A", "arguments": {"objId": "S1", "params": {}}}, rid=2)
    assert seen[-1][3]["Idempotency-Key"] != k1
    assert (await call(qa, "action_gone"))["error"]["code"] == mcp_tools.INVALID_PARAMS


async def test_metric_query_plumbs_to_evaluator(qa, monkeypatch):
    from app.modules.static import metric_trial

    seen = []

    async def fake_eval(_s, _u, code, **kw):
        seen.append((code, kw))
        return {"id": code, "rows": [{"date": "2026-10-01", "value": 1}]}

    monkeypatch.setattr(metric_trial, "evaluate_metric", fake_eval)
    out, d = payload(await call(qa, "metric_query", {"metric": "order_cnt", "days": 7, "period": "week"}))
    assert out["isError"] is False and d["id"] == "order_cnt"
    assert seen == [("order_cnt", {"days": 7, "period": "week"})]
    for bad in ({"metric": "x", "period": "year"}, {"metric": "x", "days": 94}, {"metric": ""}):
        assert (await call(qa, "metric_query", bad))["error"]["code"] == mcp_tools.INVALID_PARAMS


async def test_qa_ask_is_read_only(qa, session):
    before = await session.scalar(select(func.count()).select_from(QaTurn))
    fake = use_llm(SQL)
    out, d = payload(await call(qa, "qa_ask", {"question": "华东大区有多少家门店？"}))
    assert out["isError"] is False and d["kind"] == "answer" and d["rowCount"] == 1
    assert d["answer"]["table"]["items"] == [{"c0": 95}] and "select" not in json.dumps(d).lower()
    assert fake.prompts and await session.scalar(select(func.count()).select_from(QaTurn)) == before
    assert (await call(qa, "qa_ask", {"question": " "}))["error"]["code"] == mcp_tools.INVALID_PARAMS


async def test_qa_ask_without_model_is_tool_error(qa):
    from app.core.errors import AppError

    app.dependency_overrides[mcp_tools.mcp_llm] = lambda: AppError(
        "问数大模型已停用", code="QA_LLM_UNAVAILABLE"
    )
    out, e = payload(await call(qa, "qa_ask", {"question": "门店数"}))
    assert out["isError"] is True and e["code"] == "QA_LLM_UNAVAILABLE"
    # 模型不可用不影响其它工具
    assert (await rpc(qa, "tools/list"))["result"]["tools"]


async def test_api_key_scopes_per_tool(qa, monkeypatch):
    from app.modules.static import metric_trial

    async def fake_eval(_s, _u, code, **kw):
        return {"id": code, "rows": []}

    monkeypatch.setattr(metric_trial, "evaluate_metric", fake_eval)
    meta_only = await key(qa, "meta:read")
    names = {t["name"] for t in (await rpc(qa, "tools/list", headers=meta_only))["result"]["tools"]}
    assert "metric_query" in names and not names & {"qa_ask", "object_query"}
    denied = await call(qa, "qa_ask", {"question": "门店数"}, headers=meta_only)
    assert denied["error"]["code"] == mcp_tools.FORBIDDEN and denied["error"]["data"] == {
        "required": "objects:read"
    }
    out, _ = payload(await call(qa, "metric_query", {"metric": "order_cnt"}, headers=meta_only))
    assert out["isError"] is False

    both = await key(qa, "objects:read", "meta:read")
    names = {t["name"] for t in (await rpc(qa, "tools/list", headers=both))["result"]["tools"]}
    assert names >= QA_TOOLS
    out, d = payload(await call(qa, "object_query", {"objectType": "store", "size": 1}, headers=both))
    assert out["isError"] is False and d["total"] >= 1  # 转发时带上同一把密钥

    objects_only = await key(qa, "objects:read")  # 入口同工具清单接口：要 meta:read
    r = await qa.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=objects_only)
    assert r.status_code == 403


async def test_qa_ask_alerts_need_dynamic_read(qa, monkeypatch):
    async def fake_answer(*_a, **_k):
        return "answer", {"answer": {"card": "main", "text": "x", "alerts": [{"id": "AL1"}]}}, 0, 1, None

    monkeypatch.setattr(chain, "answer", fake_answer)
    use_llm(SQL)
    plain = await key(qa, "objects:read", "meta:read")
    _, d = payload(await call(qa, "qa_ask", {"question": "有哪些告警"}, headers=plain))
    assert "alerts" not in d["answer"] and d["answer"]["text"] == "x"
    dyn = await key(qa, "objects:read", "meta:read", "dynamic:read")
    _, d = payload(await call(qa, "qa_ask", {"question": "有哪些告警"}, headers=dyn))
    assert d["answer"]["alerts"] == [{"id": "AL1"}]
    _, d = payload(await call(qa, "qa_ask", {"question": "有哪些告警"}))  # 人工会话按页面权限
    assert d["answer"]["alerts"] == [{"id": "AL1"}]


def test_machine_action_card_hides_dry_run_without_scope():
    class _Req:
        class state:  # noqa: N801
            api_key = None

    ans = {"card": "action", "action": {"type": "A1", "dryRun": {"passed": True}, "canStage": True}}
    human = mcp_tools._action_for_machine(ans, None, _Req())
    assert human["action"]["dryRun"] == {"passed": True} and human["action"]["canStage"] is False
    key = mcp_tools._action_for_machine(ans, ("objects:read",), _Req())
    assert (
        "dryRun" not in key["action"] and key["action"]["dryRunHidden"] and key["action"]["canStage"] is False
    )
    assert ans["action"]["canStage"] is True  # 不改原卡
