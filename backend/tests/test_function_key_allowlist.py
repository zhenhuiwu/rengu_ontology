"""API-key restriction applies to both real function routes and machine tool discovery."""

import pytest
from sqlalchemy import select

from app.core import clock
from app.modules.admin.models import SysUser
from app.modules.functions.models import FnVersion, Function
from tests.use_ops_support import grant_ops_roles

B = "/api/ontology/v1"
ADMIN = {"X-Demo-As": "U010001", "X-Ontology-Id": "1"}


@pytest.fixture
async def functions(session):
    await grant_ops_roles(session)
    owner = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    for letter in ("A", "B"):
        fn = Function(
            code=f"FIX-FN-{letter}",
            name=f"fix_add_{letter.lower()}",
            title=f"实际函数{letter}",
            runtime="Python",
            status="published",
            owner_id=owner,
            timeout_s=1,
            description="真实纯计算",
            code_text=f"def fix_add_{letter.lower()}(value: int) -> int:\n    return value + 1",
            sample_in={"value": 1},
        )
        session.add(fn)
        await session.flush()
        session.add(
            FnVersion(
                function_id=fn.id, ver="v1.0.0", at=clock.now(), author_id=owner, note="测试发布", snapshot={}
            )
        )
    await session.flush()


async def create(client, *, name="fix-function-key", **body):
    result = await client.post(
        "/api/admin/v1/api-keys",
        headers=ADMIN,
        json={
            "name": name,
            "env": "测试",
            "scopes": ["meta:read", "functions:invoke"],
            "binding": {"kind": "用户", "userId": "U010001"},
            **body,
        },
    )
    assert result.status_code == 201, result.text
    data = result.json()
    return data, {"Authorization": f"Bearer {data['secret']}", "X-Ontology-Id": "1"}


async def test_both_execution_routes_and_export_obey_per_function_allowlist(client, functions):
    _, auth = await create(client, functionTypes=["FIX-FN-A"])
    for prefix, suffix in (("functions", "test"), ("machine/functions", "invoke")):
        allowed = await client.post(
            f"{B}/{prefix}/FIX-FN-A:{suffix}", headers=auth, json={"input": {"value": 2}}
        )
        assert allowed.status_code == 200 and allowed.json()["output"] == 3, allowed.text
        denied = await client.post(
            f"{B}/{prefix}/FIX-FN-B:{suffix}", headers=auth, json={"input": {"value": 2}}
        )
        assert denied.status_code == 403 and denied.json()["details"]["functionType"] == "FIX-FN-B"
    tools = await client.get(f"{B}/machine/tools", headers=auth, params={"format": "mcp"})
    assert tools.status_code == 200, tools.text
    ids = [item["id"] for item in tools.json()["bindings"].values() if item["kind"] == "function"]
    assert "FIX-FN-A" in ids and "FIX-FN-B" not in ids


async def test_null_backcompat_empty_deny_and_narrow_only_patch(client, functions):
    key, auth = await create(client)
    assert key["functionTypes"] is None
    path = f"{B}/machine/functions/FIX-FN-B:invoke"
    assert (await client.post(path, headers=auth, json={"input": {"value": 1}})).status_code == 200
    patch = f"/api/admin/v1/api-keys/{key['id']}"
    result = await client.patch(patch, headers=ADMIN, json={"functionTypes": ["FIX-FN-A"]})
    assert result.status_code == 200 and result.json()["functionTypes"] == ["FIX-FN-A"]
    assert (await client.post(path, headers=auth, json={"input": {"value": 1}})).status_code == 403
    for wider in (None, ["FIX-FN-A", "FIX-FN-B"]):
        assert (await client.patch(patch, headers=ADMIN, json={"functionTypes": wider})).status_code == 403
    assert (await client.patch(patch, headers=ADMIN, json={"functionTypes": []})).status_code == 200
    assert (
        await client.post(f"{B}/functions/FIX-FN-A:test", headers=auth, json={"input": {"value": 1}})
    ).status_code == 403


async def test_later_workspace_membership_does_not_expand_key_and_unpublished_invocation_denied(
    client, functions, session
):
    from app.modules.admin.models import ApiKey
    from app.modules.ontology.models import OntologyMember, OntologyWorkspace

    workspace = OntologyWorkspace(code="fix-function-other", name="函数权限边界空间", description="测试")
    session.add(workspace)
    await session.flush()
    owner = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    session.add(OntologyMember(ontology_id=workspace.id, user_id=owner, role="owner"))
    await session.flush()
    key, auth = await create(client, functionTypes=["FIX-FN-A"])
    assert key["ontologyIds"] == [1]
    response = await client.post(
        f"{B}/machine/functions/FIX-FN-A:invoke",
        headers={**auth, "X-Ontology-Id": str(workspace.id)},
        json={"input": {"value": 1}},
    )
    assert response.status_code == 403 and "密钥没有此本体" in response.json()["message"]
    assert (await session.scalar(select(ApiKey).where(ApiKey.code == key["id"]))).ontology_ids == [1]
    current = await session.scalar(select(Function).where(Function.code == "FIX-FN-A"))
    current.status = "draft"
    await session.flush()
    response = await client.post(
        f"{B}/machine/functions/FIX-FN-A:invoke", headers=auth, json={"input": {"value": 1}}
    )
    assert response.status_code == 409 and response.json()["code"] == "FUNCTION_NOT_PUBLISHED"


async def test_service_account_uses_same_function_restrictions_and_current_revocation(client, functions):
    account = await client.post(
        "/api/admin/v1/service-accounts",
        headers=ADMIN,
        json={"name": "fix-function-service", "roleId": "R-01", "description": "隔离函数权限验证"},
    )
    assert account.status_code == 201, account.text
    _, auth = await create(
        client,
        name="fix-service-function-key",
        functionTypes=["FIX-FN-A"],
        binding={"kind": "服务账号", "serviceAccountId": account.json()["id"]},
    )
    allowed = await client.post(
        f"{B}/machine/functions/FIX-FN-A:invoke", headers=auth, json={"input": {"value": 2}}
    )
    assert allowed.status_code == 200 and allowed.json()["output"] == 3
    assert (
        await client.post(
            f"{B}/machine/functions/FIX-FN-B:invoke", headers=auth, json={"input": {"value": 2}}
        )
    ).status_code == 403
    assert (
        await client.post(f"/api/admin/v1/service-accounts/{account.json()['id']}:revoke", headers=ADMIN)
    ).status_code == 200
    assert (
        await client.post(
            f"{B}/machine/functions/FIX-FN-A:invoke", headers=auth, json={"input": {"value": 2}}
        )
    ).status_code == 401
