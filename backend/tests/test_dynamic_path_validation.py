"""动态告警 / 动作编号的路由信任边界，不需要业务种子。"""

import pytest

from tests.function_access_support import grant_fixture_roles

API = "/api/ontology/v1"


@pytest.fixture(autouse=True)
async def function_permissions(users):
    await grant_fixture_roles(users)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "alerts/{code}", None),
        ("POST", "alerts/{code}:claim", None),
        ("POST", "alerts/{code}:transfer", {"to": "王敏"}),
        ("POST", "alerts/{code}:close", {"as": "已解决", "reason": "边界验证"}),
        ("POST", "alerts/{code}:start-action", {"params": {}}),
        ("GET", "action-runs/{code}", None),
        ("POST", "action-runs/{code}:confirm", None),
        ("POST", "action-runs/{code}:withdraw", {"reason": "边界验证"}),
        ("POST", "action-runs/{code}:approve", None),
        ("POST", "action-runs/{code}:resubmit", None),
        ("POST", "action-runs/{code}:reconcile", {"result": "仍无法确认", "note": "边界验证"}),
    ],
)
async def test_dynamic_path_rejects_nul(users, client, method, path, body):
    response = await client.request(method, f"{API}/{path.format(code='bad%00id')}", json=body)
    assert response.status_code == 422, response.text
    error = response.json()
    assert error["code"] == "VALIDATION_FAILED"
    # 对外合同移除 path 前缀；有效 body 确保这一次 422 仅由 path.code 触发。
    assert [f["field"] for f in error["details"]["fields"]] == ["code"]


@pytest.mark.parametrize("path", ["alerts", "action-runs"])
async def test_valid_missing_code_still_not_found(users, client, path):
    response = await client.get(f"{API}/{path}/MISSING-CODE")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
