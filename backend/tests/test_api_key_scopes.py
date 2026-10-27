"""admin_0025: API key scopes, allowlists, quota, service accounts, caller audit."""

import pytest
from sqlalchemy import select

from app.modules.admin.models import AuditLog, SysRole

ADMIN = {"X-Demo-As": "U010245"}
META = "/api/ontology/v1/metrics"
DRY = "/api/ontology/v1/action-types/{}:dry-run"


async def create_key(client, **body):
    payload = {
        "name": "k-" + body.pop("suffix", "a"),
        "env": "测试",
        "scopes": ["meta:read"],
        "binding": {"kind": "用户", "userId": "U010245"},
        **body,
    }
    response = await client.post("/api/admin/v1/api-keys", json=payload, headers=ADMIN)
    return response


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_scope_codes_legacy_label_and_meta_backcompat(client, seeded):
    legacy = await create_key(client, scopes=["元数据读取"], suffix="legacy")
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["scopes"] == ["meta:read"] and legacy.json()["principalKind"] == "user"
    assert (await client.get(META, headers=bearer(legacy.json()["secret"]))).status_code == 200
    # meta:read token cannot reach a scope-declared route, and undeclared routes reject tokens.
    denied = await client.post(DRY.format("X"), json={}, headers=bearer(legacy.json()["secret"]))
    assert denied.status_code == 403
    assert (
        await client.get("/api/biz/v1/orders", headers=bearer(legacy.json()["secret"]))
    ).status_code == 403
    bad = await create_key(client, scopes=["动作执行"], suffix="bad")
    assert bad.status_code == 422
    meta = (await client.get("/api/admin/v1/api-keys/meta", headers=ADMIN)).json()
    assert {o["value"] for o in meta["scopeOptions"]} == {
        "meta:read",
        "objects:read",
        "functions:invoke",
        "actions:dry-run",
        "actions:execute",
        "events:ingest",
        "dynamic:read",
    }
    listed = await client.get("/api/admin/v1/api-keys", params={"scope": "元数据读取"}, headers=ADMIN)
    assert listed.json()["total"] >= 1


async def test_action_allowlist_and_scope_dependency(client, seeded):
    key = await create_key(client, scopes=["actions:dry-run"], actionTypes=["AT-OK"], suffix="dry")
    assert key.status_code == 201, key.text
    token = key.json()["secret"]
    blocked = await client.post(DRY.format("AT-OTHER"), json={}, headers=bearer(token))
    assert blocked.status_code == 403 and blocked.json()["details"]["actionType"] == "AT-OTHER"
    allowed = await client.post(DRY.format("AT-OK"), json={}, headers=bearer(token))
    assert allowed.status_code not in (401, 403), allowed.text  # passes auth; unknown type → 404
    assert (await client.get(META, headers=bearer(token))).status_code == 403
    # Allowlist needs the matching scope.
    assert (await create_key(client, scopes=["meta:read"], eventTypes=["E1"], suffix="ev")).status_code == 422
    # Narrow-only PATCH.
    kid = key.json()["id"]
    widen = await client.patch(
        f"/api/admin/v1/api-keys/{kid}", json={"scopes": ["actions:dry-run", "meta:read"]}, headers=ADMIN
    )
    assert widen.status_code == 403
    patched = await client.patch(
        f"/api/admin/v1/api-keys/{kid}", json={"actionTypes": None, "ratePerMinute": 5}, headers=ADMIN
    )
    assert patched.status_code == 200 and patched.json()["actionTypes"] is None
    assert patched.json()["ratePerMinute"] == 5


async def test_rate_limit_429_and_usage(client, seeded):
    key = await create_key(client, ratePerMinute=2, suffix="rate")
    token = key.json()["secret"]
    assert [(await client.get(META, headers=bearer(token))).status_code for _ in range(3)] == [200, 200, 429]
    detail = (await client.get(f"/api/admin/v1/api-keys/{key.json()['id']}", headers=ADMIN)).json()
    assert detail["usage"]["minute"] == 2  # the rejected call rolled back with its request


async def test_service_account_key_revoke_and_audit_caller(client, seeded):
    role = await seeded.scalar(select(SysRole).where(SysRole.code == "R-02"))
    sa = await client.post(
        "/api/admin/v1/service-accounts",
        json={"name": "palantir-sync", "roleId": role.code, "description": "集成"},
        headers=ADMIN,
    )
    assert sa.status_code == 201, sa.text
    assert sa.json()["id"].startswith("SA-") and sa.json()["roleId"] == "R-02"
    key = await create_key(
        client,
        scopes=["meta:read", "actions:dry-run"],
        suffix="sa",
        binding={"kind": "服务账号", "serviceAccountId": sa.json()["id"]},
    )
    assert key.status_code == 201, key.text
    assert key.json()["principalKind"] == "service" and key.json()["binding"]["roleId"] == "R-02"
    token = key.json()["secret"]
    assert (await client.get(META, headers=bearer(token))).status_code == 200
    # Service role R-02 has no api-keys/roles grant: the key cannot manage keys.
    assert (await client.get("/api/admin/v1/api-keys", headers=bearer(token))).status_code == 403
    revoked = await client.post(f"/api/admin/v1/service-accounts/{sa.json()['id']}:revoke", headers=ADMIN)
    assert revoked.status_code == 200 and revoked.json()["status"] == "已吊销"
    assert (await client.get(META, headers=bearer(token))).status_code == 401
    row = await seeded.scalar(select(AuditLog).where(AuditLog.action == "吊销服务账号"))
    assert row is not None and row.principal_kind == "user" and "同时吊销密钥 1 个" in row.target


async def test_service_account_requires_role_manage(client, seeded):
    response = await client.post(
        "/api/admin/v1/service-accounts",
        json={"name": "x-sync", "roleId": "R-02"},
        headers={"X-Demo-As": "U010231"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("scope", ["nope:read"])
def test_require_api_scope_rejects_unknown(scope):
    from app.modules.admin.api_keys import require_api_scope

    with pytest.raises(ValueError):
        require_api_scope(scope)


async def test_insert_audit_records_api_caller(seeded):
    from app.modules.admin.repo import insert_audit

    seeded.info["api_caller"] = {
        "principal_kind": "service",
        "service_account_code": "SA-0009",
        "api_key_prefix": "rg_test_abcd",
    }
    try:
        row = await insert_audit(seeded, actor="sa", action="测试", target="t", ip="1.1.1.1")
    finally:
        seeded.info.pop("api_caller", None)
    assert (row.principal_kind, row.service_account_code, row.api_key_prefix) == (
        "service",
        "SA-0009",
        "rg_test_abcd",
    )


@pytest.fixture
def dynamic_probe():
    """Temporary routes declaring dynamic:read, mounted like real module routers (same guards).

    dynamic-r5 wires the real summary route the same way.
    """
    from fastapi import APIRouter, Depends

    from app.common.auth import (
        ApiKeyGuardDep,
        OntologyGuardDep,
        require_func,
    )
    from app.common.context import ReadonlyGuardDep
    from app.core.config import api_prefix
    from app.main import app
    from app.modules.admin.api_keys import require_api_scope

    probe = APIRouter()
    for page in ("alerts", "rules", "object-types", "action-center"):
        for level in ("view", "manage"):
            probe.add_api_route(
                f"/__probe/{page}/{level}",
                lambda: {"ok": True},
                methods=["GET"],
                dependencies=[Depends(require_api_scope("dynamic:read")), Depends(require_func(page, level))],
            )
    before = list(app.router.routes)
    app.include_router(
        probe,
        prefix=api_prefix("ontology"),
        dependencies=[ReadonlyGuardDep, ApiKeyGuardDep, OntologyGuardDep],
    )
    yield api_prefix("ontology") + "/__probe/{}/{}"
    app.router.routes[:] = before


async def test_dynamic_read_scope_carries_dynamic_page_reads_only(client, seeded, dynamic_probe):
    meta = (await client.get("/api/admin/v1/api-keys/meta", headers=ADMIN)).json()
    option = next(o for o in meta["scopeOptions"] if o["value"] == "dynamic:read")
    assert option["pages"] == ["action-center", "alerts", "object-types", "rules"]
    with_scope = (await create_key(client, scopes=["dynamic:read"], suffix="dyn")).json()["secret"]
    without = (await create_key(client, scopes=["objects:read"], suffix="nodyn")).json()["secret"]
    for page in ("alerts", "rules", "object-types", "action-center"):
        ok = await client.get(dynamic_probe.format(page, "view"), headers=bearer(with_scope))
        assert ok.status_code == 200, (page, ok.text)
        # view-only: manage-level checks on the same page stay closed
        assert (
            await client.get(dynamic_probe.format(page, "manage"), headers=bearer(with_scope))
        ).status_code == 403
        denied = await client.get(dynamic_probe.format(page, "view"), headers=bearer(without))
        assert denied.status_code == 403 and denied.json()["details"]["required"] == ["dynamic:read"]
    # dynamic-r6 wired dynamic:read into the alert list: the scope reaches it, other scopes do not.
    assert (await client.get("/api/ontology/v1/alerts", headers=bearer(with_scope))).status_code == 200
    assert (await client.get("/api/ontology/v1/alerts", headers=bearer(without))).status_code == 403
