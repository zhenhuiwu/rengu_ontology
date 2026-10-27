"""机器接口与入站服务账号鉴权：API 密钥 scope + 按动作/事件类型白名单（真实 PostgreSQL，无外部调用）。"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core import clock
from app.core.errors import Unauthorized
from app.modules.action.models import ActionParam
from app.modules.admin.models import ServiceAccount, SysRole
from app.modules.dynamic.models import ActionRun
from app.modules.event.models import Event, EventSource
from app.modules.machine.principal import actor_of, resolve_actor
from tests.test_event_ingress import URL, body, ingress  # noqa: F401
from tests.test_palantir_policy import set_policy
from tests.test_use_ops_runtime import API, headers, runtime  # noqa: F401

# 运行：pytest -p tests.use_ops_support tests/test_palantir_machine.py

ADMIN = {"X-Demo-As": "U010245"}


async def key(client, scopes, binding=None, **extra):
    payload = {
        "name": "k-" + uuid4().hex[:8], "env": "测试", "scopes": scopes,
        "binding": binding or {"kind": "用户", "userId": "U010001"}, **extra,
    }
    owner = {"X-Demo-As": "U010001"} if binding is None else ADMIN  # 用户密钥只能本人创建
    response = await client.post("/api/admin/v1/api-keys", json=payload, headers=owner)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['secret']}"}


async def service_key(client, scopes, **extra):
    data = {"name": "sync-" + uuid4().hex[:6], "roleId": "R-01"}
    sa = await client.post("/api/admin/v1/service-accounts", json=data, headers=ADMIN)
    assert sa.status_code == 201, sa.text
    code = sa.json()["id"]
    return code, await key(client, scopes, {"kind": "服务账号", "serviceAccountId": code}, **extra)


def execute(client, auth, type_id="ops_local", idem=None, **patch):
    data = {"objKind": "store", "objId": "OPS-S00", "params": json.dumps({"enabled": True}), **patch}
    return client.post(
        f"{API}/machine/actions/{type_id}:execute",
        json=data,
        headers={**auth, "X-Ontology-Id": "1", "Idempotency-Key": idem or uuid4().hex},
    )


# ---------- 机器执行 ----------
async def test_machine_execute_follows_policy_and_is_idempotent(runtime, client):  # noqa: F811
    session, action, _ = runtime
    auth = await key(client, ["actions:execute", "objects:read"], actionTypes=["ops_local"])
    first = await execute(client, auth, idem="m-1")
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "待确认" and first.json()["policyMode"] == "confirm"  # 默认不变
    replay = await execute(client, auth, idem="m-1")
    assert replay.status_code == 200 and replay.json()["runId"] == first.json()["runId"]
    await set_policy(session, action, {"mode": "auto", "callers": ["人"], "limits": {"dailyCount": 1}})
    done = await execute(client, auth)
    assert done.status_code == 201 and done.json()["status"] == "成功", done.text
    over = await execute(client, auth)
    assert over.json()["status"] == "待确认" and "上限 1" in over.json()["overflowReason"]
    url = f"{API}/machine/action-runs/{done.json()['runId']}"
    read = await client.get(url, headers={**auth, "X-Ontology-Id": "1"})
    assert read.status_code == 200 and read.json()["status"] == "成功", read.text


async def test_machine_execute_scope_and_action_allowlist(runtime, client):  # noqa: F811
    other = await key(client, ["actions:execute", "objects:read"], actionTypes=["ops_cross"])
    blocked = await execute(client, other)
    assert blocked.status_code == 403 and blocked.json()["details"]["actionType"] == "ops_local"
    dry_only = await key(client, ["actions:dry-run"])
    assert (await execute(client, dry_only)).status_code == 403
    no_idem = await client.post(
        f"{API}/machine/actions/ops_local:execute", json={"objId": "OPS-S00"},
        headers={**other, "X-Ontology-Id": "1"},
    )
    assert no_idem.status_code in (403, 422)


async def test_service_account_auto_executes_as_api_principal(runtime, client):  # noqa: F811
    session, action, _ = runtime
    role = await session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    assert role is not None
    code, auth = await service_key(client, ["actions:execute", "objects:read"])
    await set_policy(session, action, {"mode": "auto", "callers": ["人"], "limits": {"dailyCount": 5}})
    denied = await execute(client, auth)
    assert denied.status_code == 403 and denied.json()["code"] == "CALLER_NOT_ALLOWED", denied.text
    await set_policy(session, action, {"mode": "auto", "callers": ["服务账号"], "limits": {"dailyCount": 1}})
    done = await execute(client, auth, idem="sa-1")
    assert done.status_code == 201 and done.json()["status"] == "成功", done.text
    assert done.json()["overflowReason"] is None
    run = await session.scalar(select(ActionRun).where(ActionRun.code == done.json()["runId"]))
    assert run.created_by == f"api:{code}" and run.confirmer is None  # 执行主体是系统主体，不冒充真人
    replay = await execute(client, auth, idem="sa-1")
    assert replay.status_code == 200 and replay.json()["runId"] == done.json()["runId"]
    over = await execute(client, auth)  # 超出上限仍转人工
    assert over.status_code == 201 and over.json()["status"] == "待确认", over.text
    assert "上限 1" in over.json()["overflowReason"]
    confirmed = await client.post(f"{API}/action-runs/{over.json()['runId']}:confirm", headers=headers())
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "成功", confirmed.text


async def test_api_principal_resolves_bound_role_and_rejects_revoked(runtime, client):  # noqa: F811
    session, _, _ = runtime
    code, _ = await service_key(client, ["actions:execute"])
    user = await resolve_actor(session, f"api:{code}")
    assert user.is_service_account and user.id == code and user.roles == ("R-01",)
    assert actor_of(user) == f"api:{code}"
    with pytest.raises(Unauthorized):
        await resolve_actor(session, "api:SA-NOPE")
    account = await session.scalar(select(ServiceAccount).where(ServiceAccount.code == code))
    account.revoked_at = clock.now()
    await session.flush()
    with pytest.raises(Unauthorized):
        await resolve_actor(session, f"api:{code}")


# ---------- 入站事件：服务账号密钥 ----------
async def test_event_ingress_service_key_scope_allowlist_and_caller(ingress, client):  # noqa: F811
    session, et = ingress
    code, auth = await service_key(client, ["events:ingest"], eventTypes=[et.code])
    ok = await client.post(URL, json=body(), headers=auth)
    assert ok.status_code == 200, ok.text
    event = await session.scalar(select(Event).where(Event.code == "producer-1"))
    assert event.created_by == f"api:{code}"
    _, narrow = await service_key(client, ["events:ingest"], eventTypes=["other_event"])
    denied = await client.post(URL, json=body(eventId="producer-2"), headers=narrow)
    assert denied.status_code == 403, denied.text
    _, meta = await service_key(client, ["meta:read"])
    assert (await client.post(URL, json=body(eventId="producer-3"), headers=meta)).status_code == 403
    static_auth = {"Authorization": "Bearer isolated-test-token"}
    static = await client.post(URL, json=body(eventId="producer-4"), headers=static_auth)
    assert static.status_code == 200, static.text  # 过渡期静态令牌仍可用
    legacy = await session.scalar(select(Event).where(Event.code == "producer-4"))
    assert legacy.created_by == "internal:event"


# ---------- 第 4 期：入站只收外部来源、枚举列表参数校验 ----------
async def test_event_ingress_accepts_only_external_source(ingress, client):  # noqa: F811
    session, et = ingress
    _, auth = await service_key(client, ["events:ingest"], eventTypes=[et.code])
    static_auth = {"Authorization": "Bearer isolated-test-token"}
    for n, source in enumerate((EventSource.sync, EventSource.action, EventSource.rule)):
        et.source = source
        await session.flush()
        for m, who in enumerate((auth, static_auth)):
            res = await client.post(URL, json=body(eventId=f"src-{n}-{m}"), headers=who)
            assert res.status_code == 403 and res.json()["code"] == "EVENT_SOURCE_NOT_EXTERNAL", res.text
    assert await session.scalar(select(Event).where(Event.code.like("src-%"))) is None


async def test_enum_list_param_items_checked_on_submit(runtime, client):  # noqa: F811
    session, action, _ = runtime
    action.params.append(ActionParam(seq=1, name="标签", api="tags", type="枚举列表", rule_text="red / blue"))
    await session.flush()
    auth = await key(client, ["actions:execute", "objects:read"])
    bad = await execute(client, auth, params=json.dumps({"enabled": True, "tags": ["red", "green"]}))
    assert bad.status_code == 422 and bad.json()["code"] == "SUBMISSION_CRITERIA_FAILED", bad.text
    assert any("枚举集合" in f["fail"] for f in bad.json()["details"]["failed"]), bad.text
    ok = await execute(client, auth, params=json.dumps({"enabled": True, "tags": ["red", "blue"]}))
    assert ok.status_code == 201, ok.text
