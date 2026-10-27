"""本体小白批次 3：FastAPI 0.162 下 admin 路由识别（scope["route"].path 不再带分组前缀）。"""

import pytest
from fastapi import APIRouter, Request

from app.common.auth import CurrentUserDep, full_route_path, is_admin_request

ADMIN = {"X-Demo-As": "U010245"}


@pytest.fixture
def admin_probe():
    from app.common.auth import ApiKeyGuardDep, OntologyGuardDep
    from app.common.context import ReadonlyGuardDep
    from app.core.config import api_prefix
    from app.main import app

    probe = APIRouter()

    async def echo(code: str, request: Request, user: CurrentUserDep) -> dict:
        return {
            "relative": request.scope["route"].path,
            "full": full_route_path(request),
            "admin": is_admin_request(request),
            "ontologyId": user.ontology_id,
        }

    probe.add_api_route("/__probe/{code}", echo, methods=["GET", "POST"])
    before = list(app.router.routes)
    app.include_router(
        probe, prefix=api_prefix("admin"), dependencies=[ReadonlyGuardDep, ApiKeyGuardDep, OntologyGuardDep]
    )
    yield api_prefix("admin") + "/__probe/{}"
    app.router.routes[:] = before


async def test_full_route_path_restores_prefix(client, seeded, admin_probe):
    body = (await client.get(admin_probe.format("X1"), headers=ADMIN)).json()
    assert body["relative"] == "/__probe/{code}"  # FastAPI 0.162 行为：旧的 `"/api/admin/" in route` 判断恒假
    assert body["full"] == "/api/admin/v1/__probe/{code}" and body["admin"] is True
    assert body["ontologyId"] == 1  # 当前空间可用：照常绑定，供按本体生效的 admin 逻辑使用


@pytest.mark.parametrize("header", ["999999", "abc", "0"])
async def test_admin_route_ignores_unusable_workspace(client, seeded, admin_probe, header):
    for method in ("get", "post"):
        response = await getattr(client, method)(
            admin_probe.format("X1"), headers={**ADMIN, "X-Ontology-Id": header}
        )
        assert response.status_code == 200, response.text
        # 不按请求头绑定；身份只反映会话原有范围（测试会话预绑定空间 1，生产请求为 None）
        assert response.json()["ontologyId"] == seeded.info.get("ontology_id")
    # 真实 admin 接口同理（之前在这里报 403/404/422，导致审计导出、用户管理等用例 403）
    users = await client.get("/api/admin/v1/users", headers={**ADMIN, "X-Ontology-Id": header})
    assert users.status_code == 200, users.text


async def test_non_admin_route_still_requires_workspace(client, seeded):
    response = await client.get("/api/ontology/v1/metrics", headers={**ADMIN, "X-Ontology-Id": "999999"})
    assert response.status_code == 404
