"""外部系统页写接口（Palantir专家 第 2 批）：新建 / 编辑 / 测试调用 / 校验 / 重名 / actionCount；
事件详情带 specific。
"""

import pytest

from app.modules.integration.service import retry_text
from app.seeds.loader import run_seeds

ONT = "/api/ontology/v1"
DATA = "/api/data/v1"
ADMIN = {"X-Demo-As": "U010001"}

BODY = {
    "name": "测试短信网关",
    "vendor": "阿里云",
    "category": "消息",
    "endpoint": "https://sms.example.com/api",
    "auth": "API Key",
    "authDetail": "请求头 X-Api-Key",
    "idempotent": True,
    "idemKey": "X-Idempotency-Key",
    "timeoutMs": 5000,
    "maxRetry": 2,
    "rateLimit": "50 次/秒",
    "owner": "陈立军",
}


@pytest.fixture
async def ae(users):
    from tests.function_access_support import grant_fixture_roles

    await grant_fixture_roles(users)
    await run_seeds(users, ["integration", "action", "event"])
    await users.commit()
    return users


async def test_create_ext_system(client, ae):
    r = await client.post(f"{DATA}/ext-systems", json=BODY, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "EXT-N01"
    assert body["name"] == "测试短信网关" and body["status"] == "正常" and body["env"] == "生产"
    assert body["retry"] == retry_text(True, 2)
    # 列表能查到，actionCount = 0；第二个新建编号递增
    items = (await client.get(f"{DATA}/ext-systems", params={"size": 200}, headers=ADMIN)).json()["items"]
    new = next(s for s in items if s["id"] == "EXT-N01")
    assert new["actionCount"] == 0
    r = await client.post(f"{DATA}/ext-systems", json={**BODY, "name": "测试短信网关2"}, headers=ADMIN)
    assert r.json()["id"] == "EXT-N02"


async def test_create_without_authorization(client, ae, monkeypatch):
    import httpx
    from pydantic import SecretStr
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.modules.integration.adapters import get_adapter
    from app.modules.integration.models import ExtSystem

    r = await client.post(
        f"{DATA}/ext-systems", json={**BODY, "auth": " 无需授权 ", "authDetail": ""}, headers=ADMIN
    )
    assert r.status_code == 200, r.text
    assert r.json()["auth"] == "无需授权" and r.json()["authDetail"] == ""
    saved = await client.get(f"{DATA}/ext-systems/{r.json()['id']}", headers=ADMIN)
    assert saved.json()["auth"] == "无需授权"
    updated = await client.put(
        f"{DATA}/ext-systems/{r.json()['id']}", json={**BODY, "auth": "Token（服务账号）"}, headers=ADMIN
    )
    assert updated.status_code == 200 and updated.json()["auth"] == "Token（服务账号）"
    disabled = await client.put(
        f"{DATA}/ext-systems/{r.json()['id']}", json={**BODY, "auth": "无需授权"}, headers=ADMIN
    )
    assert disabled.status_code == 200 and disabled.json()["authDetail"] == BODY["authDetail"]
    omitted = {k: v for k, v in BODY.items() if k not in {"auth", "authDetail"}}
    kept = await client.put(f"{DATA}/ext-systems/{r.json()['id']}", json=omitted, headers=ADMIN)
    assert kept.status_code == 200 and kept.json()["auth"] == "无需授权"
    assert kept.json()["authDetail"] == BODY["authDetail"]
    system = await ae.scalar(select(ExtSystem).where(ExtSystem.code == r.json()["id"]))
    await ae.refresh(system)
    settings = get_settings()
    monkeypatch.setattr(settings, "integration_real_connect", True)
    monkeypatch.setattr(
        settings,
        "integration_adapters",
        SecretStr('{"1/EXT-N01":{"probePath":"/probe","token":"existing-token"}}'),
    )
    headers = []

    def receive(request):
        headers.append(request.headers)
        return httpx.Response(200)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(receive), **kw)
    )
    await get_adapter(system, "probe").request("probe")
    assert "Authorization" not in headers[-1]
    restored = await client.put(
        f"{DATA}/ext-systems/{r.json()['id']}", json={**BODY, "auth": "Token（服务账号）"}, headers=ADMIN
    )
    assert restored.status_code == 200 and restored.json()["authDetail"] == BODY["authDetail"]
    await ae.refresh(system)
    await get_adapter(system, "probe").request("probe")
    assert headers[-1]["Authorization"] == "Bearer existing-token"


async def test_action_count_matches_linked(client, ae):
    items = (await client.get(f"{DATA}/ext-systems", params={"size": 200}, headers=ADMIN)).json()["items"]
    erp = next(s for s in items if s["id"] == "EXT-ERP")
    assert erp["actionCount"] >= 1
    acts = (await client.get(f"{ONT}/action-types", params={"size": 200}, headers=ADMIN)).json()["items"]
    ids = [a["id"] for a in acts]
    n = 0
    for i in ids:
        d = (await client.get(f"{ONT}/action-types/{i}", headers=ADMIN)).json()
        n += d.get("sysId") == "EXT-ERP"
    assert erp["actionCount"] == n


@pytest.mark.parametrize("method", ["post", "put"])
async def test_update_ext_system(client, ae, method):
    r = await getattr(client, method)(
        f"{DATA}/ext-systems/EXT-CRM",
        json={**BODY, "name": "会员 CRM", "vendor": "有赞", "idempotent": False, "timeoutMs": 8000},
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["vendor"] == "有赞" and body["timeoutMs"] == 8000
    assert body["idemKey"] == "-" and body["maxRetry"] == 0  # 不支持幂等：不带幂等键、不自动重试
    assert (await client.get(f"{DATA}/ext-systems/EXT-CRM", headers=ADMIN)).json()["vendor"] == "有赞"
    r = await client.post(f"{DATA}/ext-systems/EXT-NOPE", json=BODY, headers=ADMIN)
    assert r.status_code == 404


async def test_validation_and_duplicate(client, ae):
    r = await client.post(
        f"{DATA}/ext-systems",
        json={**BODY, "name": " ", "vendor": "", "endpoint": "http://x", "idemKey": ""},
        headers=ADMIN,
    )
    assert r.status_code == 422
    fields = {f["field"]: f["message"] for f in r.json()["details"]["fields"]}
    assert fields["name"] == "请填写系统名称"
    assert fields["vendor"] == "请填写供应商"
    assert fields["endpoint"] == "接口地址需以 https:// 开头"
    r = await client.post(f"{DATA}/ext-systems", json={**BODY, "idemKey": " "}, headers=ADMIN)
    assert r.status_code == 422 and "幂等键" in r.text
    r = await client.post(f"{DATA}/ext-systems", json={**BODY, "timeoutMs": 500}, headers=ADMIN)
    assert r.status_code == 422
    r = await client.post(f"{DATA}/ext-systems", json={**BODY, "name": "会员 CRM"}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["code"] == "DUPLICATE_KEY"
    # 改成自己的名字不算重名；改成别人的名字算
    ok = await client.post(f"{DATA}/ext-systems/EXT-CRM", json={**BODY, "name": "会员 CRM"}, headers=ADMIN)
    assert ok.status_code == 200
    dup = await client.post(f"{DATA}/ext-systems/EXT-CRM", json={**BODY, "name": "用友 ERP"}, headers=ADMIN)
    assert dup.status_code == 409


async def test_write_rejected_in_readonly(client, ae):
    # 功能权限（external-systems · manage）由 require_func 承接，校验本身是 PDE 的扩展点，这里只验只读模式
    r = await client.post(f"{DATA}/ext-systems", params={"previewAs": "U010006"}, json=BODY, headers=ADMIN)
    assert r.status_code == 403 and r.json()["code"] == "READONLY"


async def test_test_call(client, ae):
    items = (await client.get(f"{DATA}/ext-systems", params={"size": 200}, headers=ADMIN)).json()["items"]
    by_status = {s["status"]: s["id"] for s in items}
    for code in ("EXT-CRM", by_status["降级"]):
        r = await client.post(f"{DATA}/ext-systems/{code}:test", headers=ADMIN)
        assert r.status_code == 503 and r.json()["code"] == "EXT_SYSTEM_TEST_UNAVAILABLE"
        assert r.json()["details"] == {"systemId": code, "reason": "executor_not_configured"}
    r = await client.post(f"{DATA}/ext-systems/{by_status['停用']}:test", headers=ADMIN)
    assert r.status_code == 409 and r.json()["code"] == "EXT_SYSTEM_DISABLED"
    assert (await client.post(f"{DATA}/ext-systems/EXT-NOPE:test", headers=ADMIN)).status_code == 404


async def test_event_detail_has_specific(client, ae):
    r = await client.get(f"{ONT}/event-types/order_status_changed", headers=ADMIN)
    spec = r.json()["specific"]
    path = f"{ONT}/event-types/order_status_changed/payload-fields"
    items = (await client.get(path, params={"size": 200})).json()["items"]
    assert spec == [{k: v for k, v in f.items() if k != "envelope"} for f in items if not f["envelope"]]
