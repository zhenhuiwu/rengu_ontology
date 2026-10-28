"""真实审核候选与稳定身份；姓名变化不能夺取或丢失草稿权限。"""

import pytest
from sqlalchemy import select

from app.modules.admin.models import SysUser
from app.modules.static.models import (
    ConflictStrategy,
    Lifecycle,
    Metric,
    MetricStatus,
    ObjectStatus,
    ObjectType,
    Property,
)
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1"
AUTHOR = {"X-Demo-As": "U010001"}
REVIEWER = {"X-Demo-As": "U010004"}


@pytest.fixture
async def identities(session):
    await run_seeds(session, ["admin"])
    await grant_fixture_roles(session)
    # This test's existing metric target needs a real compiled mapping, not a missing baseline.
    order = await session.scalar(select(ObjectType).where(ObjectType.code == "order"))
    if order is None:
        order = ObjectType(
            code="order",
            api_name="SalesOrder",
            display_name="订单",
            obj_group="测试",
            status=ObjectStatus.published,
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=Lifecycle.active,
        )
        session.add(order)
        await session.flush()
    session.add(
        Property(
            object_type_id=order.id,
            api_name="review_created_at",
            display_name="创建时间",
            data_type="datetime",
            prop_group="测试",
            source_column="created_at_biz",
            backing_column="created_at_biz",
        )
    )
    session.add(
        Metric(
            code="review_metric",
            api_name="ReviewMetric",
            display_name="审核流程指标",
            metric_group="测试",
            unit="个",
            version="v1",
            status=MetricStatus.published,
            owner="王敏",
            formula="COUNT(order.review_created_at)",
        )
    )
    await session.flush()
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
    validated = await client.post(f"{P}/change-requests/{cr['id']}:validate", headers=AUTHOR)
    assert validated.status_code == 200, validated.text
    checks = (await client.get(f"{P}/change-requests/{cr['id']}", headers=AUTHOR)).json()["detail"]["checks"]
    assert next(row for row in checks if row["code"] == "V5")["result"] == "通过"
    submitted = await client.post(f"{P}/change-requests/{cr['id']}:submit", headers=AUTHOR)
    assert submitted.status_code == 200, submitted.text
    self_approval = await client.post(f"{P}/change-requests/{cr['id']}:approve", headers=AUTHOR)
    assert self_approval.status_code == 403, self_approval.text
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
    assert reviewer["roles"] == ["R-02"]
    assert reviewer["func"]["releases"] == "manage"
    assert reviewer["capabilities"] == {"publish": True, "rollback": True}
    for profile in (admin.json(), reviewer):
        assert profile["capabilities"]["publish"] == (profile["func"].get("releases") == "manage")
    denied = (await client.get("/api/admin/v1/me", headers={"X-Demo-As": "U010006"})).json()
    assert denied["roles"] == [] and denied["capabilities"] == {"publish": False, "rollback": False}
