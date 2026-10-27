"""第 3 期机器接口：对象查询、函数调用别名、AI 工具清单（能力目录转换）、执行响应补字段、动作意图评测集。

运行：pytest -p tests.use_ops_support tests/test_palantir_machine_tools.py（真实 PostgreSQL，无外部调用）
"""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import select

from app.modules.action.models import ActionParam
from app.modules.admin.models import SysUser
from app.modules.event.models import EventPayloadField, EventSource
from app.modules.functions.models import FnParam, Function
from tests.qa_access_support import seed_qa_access
from tests.test_event_ingress import event_type
from tests.test_palantir_machine import execute, key, service_key
from tests.test_palantir_policy import set_policy
from tests.test_use_ops_runtime import API, headers, runtime  # noqa: F401
from tests.use_ops_support import bind_fixture_members

EVALS = Path(__file__).parent / "fixtures" / "palantir_action_intents.yaml"
ONT = {"X-Ontology-Id": "1"}
SOURCE = "def calculate(price: float, qty: int) -> float:\n    return price * qty"


def valid(schema: dict, value, path="$") -> list[str]:
    """测试用 JSON Schema 子集校验：type、enum、required、properties、additionalProperties、items。"""
    kinds = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
    }
    errors = []
    t = schema.get("type")
    if t and (not isinstance(value, kinds[t]) or (t in ("integer", "number") and isinstance(value, bool))):
        return [f"{path}: 应为 {t}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 不在 {schema['enum']}")
    if t == "object":
        props = schema.get("properties", {})
        errors += [f"{path}.{k}: 缺少" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{path}.{k}: 多余" for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                errors += valid(props[k], v, f"{path}.{k}")
    if t == "array" and "items" in schema:
        for i, v in enumerate(value):
            errors += valid(schema["items"], v, f"{path}[{i}]")
    return errors


@pytest.fixture
async def extras(runtime):  # noqa: F811
    """在运维夹具上补一个外部事件类型和两个函数（已发布 / 草稿）。"""
    session, action, cross = runtime
    et = event_type("ops_signal")
    et.api, et.name, et.object, et.object_api, et.obj_kind = "OpsSignal", "门店信号", "门店", "Store", "store"
    session.add(et)
    owner = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    fns = []
    for code, name, status in (("FN-OPS-1", "calculate", "published"), ("FN-OPS-2", "draft_calc", "draft")):
        fn = Function(
            code=code,
            name=name,
            title="总额",
            runtime="受限纯计算表达式",
            status=status,
            owner_id=owner,
            timeout_s=5,
            description="单价乘数量",
            code_text=SOURCE.replace("calculate", name),
            sample_in={},
            sample_out=None,
        )
        session.add(fn)
        fns.append(fn)
    await session.flush()
    session.add(
        EventPayloadField(event_type_id=et.id, seq=0, name="等级", api="level", type="整数", required=True)
    )
    for fn in fns:
        session.add_all(
            [
                FnParam(function_id=fn.id, direction="in", seq=0, name="price", type="float", desc="单价"),
                FnParam(function_id=fn.id, direction="in", seq=1, name="qty", type="int", desc="数量"),
                FnParam(function_id=fn.id, direction="out", seq=0, name="result", type="float", desc=""),
            ]
        )
    await session.flush()
    return session, action, cross


async def tools_of(client, auth, fmt="mcp"):
    res = await client.get(f"{API}/machine/tools?format={fmt}", headers={**auth, **ONT})
    assert res.status_code == 200, res.text
    return res.json()


def by_name(body):
    return {t["name"]: t for t in body["tools"]}


ALL = ["meta:read", "objects:read", "actions:execute", "events:ingest", "functions:invoke"]


# ---------- 对象查询 ----------
async def test_objects_query_reuses_explore_and_row_scope(users, client):
    await seed_qa_access(users)
    await bind_fixture_members(users)  # 授权后重算工作空间成员角色
    await users.flush()
    human = await client.post(
        "/api/qa/v1/object-types/store/search",
        json={"size": 200, "with_facets": False},
        headers={"X-Demo-As": "U010001"},
    )
    assert human.status_code == 200, human.text
    auth = {**(await key(client, ["objects:read"])), **ONT}
    url = f"{API}/machine/objects:query"
    res = await client.post(url, json={"objectType": "store", "size": 200}, headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["bound"] and body["total"] == human.json()["total"] > 0  # 同一套行级范围
    assert [i["id"] for i in body["items"]] == [i["id"] for i in human.json()["items"]]
    assert body["facets"] == []
    page = await client.post(
        url, json={"objectType": "store", "size": 5, "page": 2, "sort": "-area_sqm"}, headers=auth
    )
    assert page.status_code == 200 and len(page.json()["items"]) == 5, page.text
    bad = await client.post(url, json={"objectType": "store", "filters": {"no_such": [1]}}, headers=auth)
    assert bad.status_code == 422
    meta_only = {**(await key(client, ["meta:read"])), **ONT}
    assert (await client.post(url, json={"objectType": "store"}, headers=meta_only)).status_code == 403
    _, sa = await service_key(client, ["objects:read"])
    sa_res = await client.post(url, json={"objectType": "store", "size": 1}, headers={**sa, **ONT})
    assert sa_res.status_code == 200 and sa_res.json()["bound"], sa_res.text  # 服务账号按绑定角色范围


# ---------- 函数调用别名 ----------
async def test_function_invoke_alias_published_only(extras, client):
    auth = {**(await key(client, ["functions:invoke"])), **ONT}
    ok = await client.post(
        f"{API}/machine/functions/FN-OPS-1:invoke", json={"input": {"price": 2.5, "qty": 4}}, headers=auth
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["output"] == 10.0 and ok.json()["functionId"] == "FN-OPS-1"
    draft = await client.post(f"{API}/machine/functions/FN-OPS-2:invoke", json={"input": {}}, headers=auth)
    assert draft.status_code == 409 and draft.json()["code"] == "FUNCTION_NOT_PUBLISHED"
    missing = await client.post(f"{API}/machine/functions/FN-NONE:invoke", json={"input": {}}, headers=auth)
    assert missing.status_code == 404
    meta = {**(await key(client, ["meta:read"])), **ONT}
    denied = await client.post(f"{API}/machine/functions/FN-OPS-1:invoke", json={"input": {}}, headers=meta)
    assert denied.status_code == 403


# ---------- 工具清单 ----------
async def test_tools_export_formats_and_schemas(extras, client):
    body = await tools_of(client, headers())  # 人工会话：按页面权限
    tools = by_name(body)
    assert {"action_OpsLocal", "action_OpsCross", "event_OpsSignal", "fn_calculate"} <= set(tools)
    assert not any(n.startswith("event_Action") for n in tools)  # 动作来源事件不当工具
    assert "fn_draft_calc" not in tools  # 只导出已发布函数
    local = tools["action_OpsLocal"]["inputSchema"]
    assert local["properties"]["objKind"]["enum"] == ["store"]
    assert local["properties"]["params"]["properties"]["enabled"]["type"] == "boolean"
    assert local["properties"]["params"]["required"] == ["enabled"]
    cross = tools["action_OpsCross"]["inputSchema"]["properties"]["params"]
    assert cross["properties"]["otherStore"]["type"] == "string" and cross["required"] == [
        "enabled",
        "otherStore",
    ]
    fn = tools["fn_calculate"]["inputSchema"]["properties"]["input"]["properties"]
    assert fn == {
        "price": {"type": "number", "description": "单价"},
        "qty": {"type": "integer", "description": "数量"},
    }
    event = tools["event_OpsSignal"]["inputSchema"]["properties"]
    assert event["objectType"]["enum"] == ["Store", "store"] and event["payload"]["required"] == ["level"]
    bind = body["bindings"]["action_OpsLocal"]
    assert bind["path"] == f"{API}/machine/actions/ops_local:execute" and bind["policyMode"] == "confirm"
    openai = by_name(
        {"tools": [t["function"] for t in (await tools_of(client, headers(), "openai"))["tools"]]}
    )
    assert openai["action_OpsLocal"]["parameters"] == local
    assert (await client.get(f"{API}/machine/tools?format=xml", headers=headers())).status_code == 422


async def test_tools_take_enum_and_events_from_capability_catalog(extras, client):
    session, action, _ = extras
    action.params.append(ActionParam(seq=1, name="标签", api="tags", type="枚举列表", rule_text="red / blue"))
    synced = event_type("ops_synced")
    synced.api, synced.source = "OpsSynced", EventSource.sync  # 已发布但非外部来源：入站拒收，不导出
    session.add(synced)
    await session.flush()
    catalog = await client.get(f"{API}/meta/capabilities", headers=headers())
    assert catalog.status_code == 200, catalog.text
    events = {e["id"]: e["ingest"] for e in catalog.json()["events"]}
    assert events["ops_signal"]["accepting"] and not events["ops_synced"]["accepting"]
    body = await tools_of(client, headers())
    tools = by_name(body)
    tags = tools["action_OpsLocal"]["inputSchema"]["properties"]["params"]["properties"]["tags"]
    assert tags["items"]["enum"] == ["red", "blue"]  # 目录 enum，工具侧不再解析参数规则
    assert "event_OpsSynced" not in tools and "event_OpsSignal" in tools
    bind = body["bindings"]["event_OpsSignal"]
    signal = events["ops_signal"]
    assert [bind[k] for k in ("method", "path", "scope")] == [signal[k] for k in ("method", "path", "scope")]
    blocked = await key(client, ["meta:read", "events:ingest"], eventTypes=["OpsSynced"])
    assert not any(n.startswith("event_") for n in by_name(await tools_of(client, blocked)))  # keyAllowed


async def test_tools_respect_key_scopes_allowlists_and_policy(extras, client):
    session, action, _ = extras
    only_local = await key(client, ["meta:read", "actions:execute"], actionTypes=["ops_local"])
    assert set(by_name(await tools_of(client, only_local))) == {"action_OpsLocal"}
    events = await key(client, ["meta:read", "events:ingest"], eventTypes=["OpsSignal"])
    assert set(by_name(await tools_of(client, events))) == {"event_OpsSignal"}
    fns = await key(client, ["meta:read", "functions:invoke"])
    assert set(by_name(await tools_of(client, fns))) == {"fn_calculate"}
    no_meta = {**(await key(client, ["actions:execute"])), **ONT}
    assert (await client.get(f"{API}/machine/tools", headers=no_meta)).status_code == 403
    every = await key(client, ALL)
    await set_policy(session, action, {"mode": "suggest"})
    assert "action_OpsLocal" not in by_name(await tools_of(client, every))
    await set_policy(session, action, {"mode": "auto", "callers": ["服务账号"], "limits": {"dailyCount": 2}})
    assert "action_OpsLocal" not in by_name(await tools_of(client, every))  # 用户密钥 = 人
    _, sa = await service_key(client, ALL)
    sa_tools = await tools_of(client, sa)
    assert sa_tools["bindings"]["action_OpsLocal"]["policyMode"] == "auto"


# ---------- 执行响应补字段 ----------
async def test_execute_response_checks_edits_retryable(runtime, client):  # noqa: F811
    auth = await key(client, ["actions:execute", "objects:read"])
    res = await execute(client, auth, params={"enabled": True})  # 参数可直接给对象
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["checks"] == {"passed": True, "checked": 0}
    assert len(body["edits"]) == 1 and body["edits"][0]["obj"] == "store"
    assert set(body["edits"][0]) == {"op", "obj", "field"}
    assert isinstance(body["retryable"], bool)


# ---------- 动作意图评测集 ----------
async def test_action_intent_eval_set_matches_tool_export(extras, client):
    session, action, _ = extras
    data = yaml.safe_load(EVALS.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) == 20 and len({c["id"] for c in cases}) == 20
    kinds = {c["expect"]["kind"] for c in cases}
    assert kinds == {"call", "clarify", "refuse", "confirm"}
    for case in cases:
        setup, expect = case.get("setup") or {}, case["expect"]
        await set_policy(session, action, setup.get("policy"))  # None = 默认人工确认
        extra = {"actionTypes": setup["keyActionTypes"]} if "keyActionTypes" in setup else {}
        auth = await key(client, ALL, **extra)
        body = await tools_of(client, auth)
        tools = by_name(body)
        for absent in expect.get("absent", []):
            assert absent not in tools, case["id"]
        if expect.get("tool"):
            assert expect["tool"] in tools, case["id"]
            schema = tools[expect["tool"]]["inputSchema"]
        if expect["kind"] in ("call", "confirm"):
            assert valid(schema, expect["args"]) == [], (case["id"], valid(schema, expect["args"]))
        if expect["kind"] == "clarify" and expect.get("tool"):
            for missing in expect["missing"]:
                node = schema
                *parents, leaf = missing.split(".")
                for p in parents:
                    node = node["properties"][p]
                assert leaf in node["required"], case["id"]
        if expect["kind"] == "confirm":
            for _ in range(setup.get("prior", 0)):
                done = await execute(client, auth)
                assert done.json()["status"] == "成功", (case["id"], done.text)
            bind = body["bindings"][expect["tool"]]
            res = await client.post(
                bind["path"], json=expect["args"], headers={**auth, **ONT, "Idempotency-Key": uuid4().hex}
            )
            assert res.status_code == 201 and res.json()["status"] == "待确认", (case["id"], res.text)
