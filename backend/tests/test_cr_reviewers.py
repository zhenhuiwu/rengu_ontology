"""真实审核候选与稳定身份；姓名变化不能夺取或丢失草稿权限。"""

import pytest
from sqlalchemy import select

from app.modules.admin.models import SysUser
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1"
AUTHOR = {"X-Demo-As": "U010001"}
REVIEWER = {"X-Demo-As": "U010004"}


@pytest.fixture
async def identities(session):
    await run_seeds(session, ["admin"])
    await grant_fixture_roles(session)
    return session


async def create(client):
    response = await client.post(
        P + "/change-requests",
        headers=AUTHOR,
        json={"title": "真实审核流程", "target": {"kind": "metric", "id": "review_metric"}},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_reviewer_options_patch_and_approval_use_stable_identity(client, identities):
    cr = await create(client)
    assert cr["reviewer"] == "" and cr["ownerId"] == "U010001"
    user = await identities.scalar(select(SysUser).where(SysUser.code == "U010001"))
    user.name = "实际提交人新姓名"
    await identities.flush()
    response = await client.get(f"{P}/change-requests/{cr['id']}/reviewer-options", headers=AUTHOR)
    assert response.status_code == 200, response.text
    eligible = {u["id"] for u in response.json()}
    assert {"U010002", "U010004"} <= eligible
    assert "U010001" not in eligible and "U010006" not in eligible
    assert all(set(u) == {"id", "name"} for u in response.json())
    selected = await client.patch(
        f"{P}/change-requests/{cr['id']}",
        headers=AUTHOR,
        json={"reviewer": "U010004", "lockVersion": cr["lockVersion"]},
    )
    assert selected.status_code == 200, selected.text
    cr = selected.json()
    assert cr["reviewerId"] == "U010004" and cr["reviewer"] == "刘婷"
    result = await client.put(
        f"{P}/change-requests/{cr['id']}/items",
        headers=AUTHOR,
        json={
            "target": {"kind": "metric", "id": "review_metric"},
            "section": "def",
            "rows": [{"key": "unit", "op": "update", "after": "元"}],
            "lockVersion": cr["lockVersion"],
        },
    )
    assert result.status_code == 200, result.text
    submitted = await client.post(f"{P}/change-requests/{cr['id']}:submit", headers=AUTHOR)
    assert submitted.status_code == 200, submitted.text
    reviewer = await identities.scalar(select(SysUser).where(SysUser.code == "U010004"))
    reviewer.name = "实际审核人新姓名"
    await identities.flush()
    response = await client.post(f"{P}/change-requests/{cr['id']}:approve", headers=REVIEWER)
    assert response.status_code == 200, response.text


async def test_invalid_reviewer_owner_readonly_and_stale_lock_preserve_header(client, identities):
    cr = await create(client)
    path = f"{P}/change-requests/{cr['id']}"
    for code in ("U010001", "U010006"):
        response = await client.patch(
            path, headers=AUTHOR, json={"reviewer": code, "lockVersion": cr["lockVersion"]}
        )
        assert response.status_code == 403, response.text
    response = await client.patch(
        path, headers=REVIEWER, json={"reviewer": "U010002", "lockVersion": cr["lockVersion"]}
    )
    assert response.status_code == 403
    selected = await client.patch(
        path, headers=AUTHOR, json={"reviewer": "U010004", "lockVersion": cr["lockVersion"]}
    )
    assert selected.status_code == 200
    stale = await client.patch(
        path, headers=AUTHOR, json={"reviewer": "U010002", "lockVersion": cr["lockVersion"]}
    )
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    for query in ("?ver=v2026.10.1", "?previewAs=U010004"):
        response = await client.patch(path + query, headers=AUTHOR, json={"reviewer": "U010002"})
        assert response.status_code == 403 and response.json()["code"] == "READONLY"
    assert (await client.get(path, headers=AUTHOR)).json()["reviewerId"] == "U010004"


async def test_me_capabilities_come_from_actual_identity_and_roles(client, identities):
    admin = await client.get("/api/admin/v1/me", headers=AUTHOR)
    assert admin.status_code == 200
    assert admin.json()["id"] == "U010001" and admin.json()["roles"] == ["R-01"]
    assert admin.json()["capabilities"] == {"publish": True, "rollback": True}
    reviewer = (await client.get("/api/admin/v1/me", headers=REVIEWER)).json()
    assert reviewer["capabilities"] == {"publish": False, "rollback": True}
    denied = (await client.get("/api/admin/v1/me", headers={"X-Demo-As": "U010006"})).json()
    assert denied["roles"] == [] and denied["capabilities"] == {"publish": False, "rollback": False}
