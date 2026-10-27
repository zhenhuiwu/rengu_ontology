"""动态模块鉴权：每个接口（含 GET）都依赖 CurrentUserDep + require_func，未知 / 停用身份一律 401。"""

import re

from fastapi.routing import APIRoute

from app.common.auth import get_current_user
from tests.dynamic_support import API, dyn  # noqa: F401

UNKNOWN = "U999999"  # sys_user 里没有
DISABLED = "U010308"  # 高伟：ADMIN_USERS.json status=停用


def _dynamic_routes() -> list[APIRoute]:
    # app.routes 里是惰性挂载的 _IncludedRouter，直接看模块 router（路径不含 /api/ontology/v1 前缀）
    from app.modules.dynamic.router import router
    from app.modules.dynamic.rules_router import router as rules_router  # include_router 后也是惰性挂载

    routes = [r for r in [*router.routes, *rules_router.routes] if isinstance(r, APIRoute)]
    # Inspect every current route, including new launch metadata/preflight, rather than an obsolete count.
    assert {"/actions/available", "/actions/{code}/targets", "/actions/{code}:validate"} <= {
        r.path for r in routes
    }
    assert len(routes) == len({(r.path, tuple(sorted(r.methods))) for r in routes})
    return routes


def _calls(dependant) -> set:
    out = {dependant.call}
    for d in dependant.dependencies:
        out |= _calls(d)
    return out


def test_every_route_depends_on_current_user_and_require_func():
    for r in _dynamic_routes():
        calls = _calls(r.dependant)
        assert get_current_user in calls, r.path
        names = {getattr(c, "__qualname__", "") for c in calls}
        assert any(n.startswith("require_func.") for n in names), r.path


async def test_unknown_or_disabled_user_gets_401_on_every_route(dyn, client):  # noqa: F811
    for r in _dynamic_routes():
        path = API + re.sub(r"\{[^}]+\}", "X-NOPE", r.path)
        for method in r.methods:
            for who in (UNKNOWN, DISABLED):
                resp = await client.request(method, path, headers={"X-Demo-As": who}, json={})
                assert resp.status_code == 401, (method, path, who, resp.text)
                assert resp.json()["code"] == "UNAUTHORIZED", (method, path, who)
    # 正常身份能读（确认 401 不是路由本身坏了）
    ok = await client.get(f"{API}/alerts", headers={"X-Demo-As": "U010001"})
    assert ok.status_code == 200


async def test_validation_failed_shape(dyn, client):  # noqa: F811
    """服务层抛的和 FastAPI 参数校验的 422 都是 VALIDATION_FAILED + details.fields=[{field, message}]。"""
    h = {"X-Demo-As": "U010001"}
    for url, field in ((f"{API}/action-runs?tab=nope", "tab"), (f"{API}/alerts?size=0", "size")):
        body = (await client.get(url, headers=h)).json()
        assert body["code"] == "VALIDATION_FAILED", body
        fields = body["details"]["fields"]
        assert fields and all(set(f) >= {"field", "message"} for f in fields), body
        assert any(f["field"].endswith(field) for f in fields), body
