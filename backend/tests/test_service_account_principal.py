"""本体小白批次 3：服务账号身份的公开构造函数 + 请求内 CurrentUser 的机器调用方字段。"""

import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import delete, select

from app.common.auth import CurrentUserDep
from app.core.errors import Unauthorized
from app.modules.admin.api_keys import (
    _service_account_user,
    build_service_account_principal,
    require_api_scope,
)
from app.modules.admin.models import ServiceAccount, SysRole

ADMIN = {"X-Demo-As": "U010245"}


async def make_account(client, seeded, name="pal-sync"):
    response = await client.post(
        "/api/admin/v1/service-accounts", json={"name": name, "roleId": "R-02"}, headers=ADMIN
    )
    assert response.status_code == 201, response.text
    return await seeded.scalar(select(ServiceAccount).where(ServiceAccount.code == response.json()["id"]))


async def test_build_by_code_and_instance(client, seeded):
    account = await make_account(client, seeded)
    role = await seeded.scalar(select(SysRole).where(SysRole.code == "R-02"))
    by_code = await build_service_account_principal(seeded, account.code)
    by_obj = await build_service_account_principal(seeded, account)
    assert by_code == by_obj
    assert by_code.id == account.code and by_code.is_service_account
    assert by_code.roles == ("R-02",) and by_code.func == role.func
    assert by_code.service_account_id == account.id
    assert by_code.ontology_id == 1 and by_code.ontology_role == "service"
    assert isinstance(by_code.row_filters, dict)
    unbound = await build_service_account_principal(seeded, account.code, bind_ontology=False)
    assert unbound.ontology_id is None and unbound.row_filters == {}
    assert _service_account_user is build_service_account_principal


async def test_revoked_missing_and_unbound_fail(client, seeded):
    account = await make_account(client, seeded, "pal-revoke")
    with pytest.raises(Unauthorized):
        await build_service_account_principal(seeded, "SA-NOPE")
    from app.modules.ontology.models import OntologyRoleBinding

    await seeded.execute(delete(OntologyRoleBinding).where(OntologyRoleBinding.role_id == account.role_id))
    with pytest.raises(Unauthorized):
        await build_service_account_principal(seeded, account.code)
    revoked = await client.post(f"/api/admin/v1/service-accounts/{account.code}:revoke", headers=ADMIN)
    assert revoked.status_code == 200, revoked.text
    for kwargs in ({}, {"bind_ontology": False}):
        with pytest.raises(Unauthorized):
            await build_service_account_principal(seeded, account.code, **kwargs)


@pytest.fixture
def whoami():
    from app.common.auth import ApiKeyGuardDep, OntologyGuardDep
    from app.common.context import ReadonlyGuardDep
    from app.core.config import api_prefix
    from app.main import app

    probe = APIRouter()

    async def me(user: CurrentUserDep) -> dict:
        return {
            "id": user.id,
            "isServiceAccount": user.is_service_account,
            "serviceAccountId": user.service_account_id,
            "prefix": user.api_key_prefix,
            "scopes": list(user.api_scopes or ()),
            "actions": user.action_allowlist,
            "events": user.event_allowlist,
            "quota": user.quota,
            "roles": list(user.roles),
            "ontologyRole": user.ontology_role,
        }

    probe.add_api_route(
        "/__probe/whoami", me, methods=["GET"], dependencies=[Depends(require_api_scope("actions:dry-run"))]
    )
    before = list(app.router.routes)
    app.include_router(
        probe,
        prefix=api_prefix("ontology"),
        dependencies=[ReadonlyGuardDep, ApiKeyGuardDep, OntologyGuardDep],
    )
    yield api_prefix("ontology") + "/__probe/whoami"
    app.router.routes[:] = before


async def test_request_principal_exposes_key_context(client, seeded, whoami):
    account = await make_account(client, seeded, "pal-ctx")
    key = await client.post(
        "/api/admin/v1/api-keys",
        json={
            "name": "k-ctx",
            "env": "测试",
            "scopes": ["actions:dry-run"],
            "actionTypes": ["AT-OK"],
            "ratePerMinute": 10,
            "binding": {"kind": "服务账号", "serviceAccountId": account.code},
        },
        headers=ADMIN,
    )
    assert key.status_code == 201, key.text
    body = key.json()
    response = await client.get(whoami, headers={"Authorization": f"Bearer {body['secret']}"})
    assert response.status_code == 200, response.text
    me = response.json()
    assert me["id"] == account.code and me["isServiceAccount"] is True
    assert me["serviceAccountId"] == account.id and me["prefix"] == body["prefix"]
    assert me["scopes"] == ["actions:dry-run"] and me["actions"] == ["AT-OK"] and me["events"] is None
    assert me["quota"] == {"minute": {"limit": 10, "used": 1}, "day": {"limit": None, "used": None}}
    assert me["roles"] == ["R-02"] and me["ontologyRole"] == "service"
    # 真人会话：机器字段全空
    human = (await client.get(whoami, headers=ADMIN)).json()
    assert human["isServiceAccount"] is False and human["prefix"] is None and human["quota"] is None
