"""机器接口与入站服务账号鉴权：API 密钥 scope + 按动作/事件类型白名单（真实 PostgreSQL，无外部调用）。"""

import json
from uuid import uuid4

from sqlalchemy import select

from app.modules.action import policy as action_policy
from app.modules.admin.models import SysRole
from app.modules.event.models import Event
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


async def test_service_account_caller_is_gated_by_policy(runtime, client):  # noqa: F811
    session, action, _ = runtime
    role = await session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    assert role is not None
    _, auth = await service_key(client, ["actions:execute", "objects:read"])
    await set_policy(session, action, {"mode": "auto", "callers": ["人"], "limits": {"dailyCount": 5}})
    denied = await execute(client, auth)
    assert denied.status_code == 403 and denied.json()["code"] == "CALLER_NOT_ALLOWED", denied.text
    await set_policy(session, action, {"mode": "auto", "callers": ["服务账号"], "limits": {"dailyCount": 5}})
    staged = await execute(client, auth)
    assert staged.status_code == 201 and staged.json()["status"] == "待确认", staged.text
    assert staged.json()["overflowReason"] == action_policy.SERVICE_ACCOUNT_TEXT
    # 转人工：任一有动作中心管理权限的人可确认（服务账号不是确认人）
    done = await client.post(f"{API}/action-runs/{staged.json()['runId']}:confirm", headers=headers())
    assert done.status_code == 200 and done.json()["status"] == "成功", done.text


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
