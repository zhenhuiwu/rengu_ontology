"""健康检查、请求 ID、统一错误体、ver/preview 只读守卫。"""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.common.context import WritableDep
from app.core.errors import install_error_handlers
from app.core.request_id import RequestIdMiddleware


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert len(r.headers["x-request-id"]) == 32


async def test_readyz_checks_db(client):
    r = await client.get("/readyz")
    assert r.json() == {"status": "ok", "db": "ok"}


async def test_client_422_does_not_poison_seeded_session(client, users):
    """同一 client 里一次 422 不能把 users / seeded 回滚掉（否则后续请求 401）。

    get_session 覆盖用 begin_nested：错误响应只撤这次请求的 SAVEPOINT。
    """
    bad = await client.get("/api/biz/v1/orders", params={"size": 0})
    assert bad.status_code == 422 and bad.json()["code"] == "VALIDATION_FAILED"
    ok = await client.get("/api/biz/v1/orders", params={"size": 1})
    assert ok.status_code == 200, ok.json()  # 仍能解析 X-Demo-As / RENGU_DEV_USER → sys_user


async def test_request_id_is_echoed_into_error_body(client, users):
    r = await client.get("/api/ontology/v1/releases/v1999.01.1", headers={"X-Request-ID": "rid-123"})
    assert r.status_code == 404
    assert r.headers["x-request-id"] == "rid-123"
    assert r.json() == {
        "code": "NOT_FOUND",
        "message": "版本不存在",
        "details": {"ver": "v1999.01.1"},
        "requestId": "rid-123",
    }


def _probe_app() -> FastAPI:
    from fastapi import APIRouter, Query

    from app.common.context import ReadonlyGuardDep, allow_in_readonly, readonly_extra

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)
    r = APIRouter()

    @r.get("/v")
    async def v(size: int = Query(ge=1)) -> dict:
        return {"size": size}

    @r.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("x")

    @r.post("/w")
    async def w_post() -> dict:
        return {"ok": True}

    @r.put("/w")
    async def w_put() -> dict:
        return {"ok": True}

    @r.patch("/w")
    async def w_patch() -> dict:
        return {"ok": True}

    @r.delete("/w")
    async def w_delete() -> dict:
        return {"ok": True}

    @r.post("/rules/{rid}:dry-run")
    @allow_in_readonly
    async def dry_run(rid: str) -> dict:
        return {"ok": rid}

    @r.post(
        "/permission-requests", openapi_extra=readonly_extra("history", "preview")
    )  # qa W16 申请权限：落库但前端允许
    async def ask() -> dict:
        return {"ok": True}

    @r.post("/history-only")
    @allow_in_readonly(modes={"history"})
    async def history_only() -> dict:
        return {"ok": True}

    app.include_router(r, prefix="/api/x/v1", dependencies=[ReadonlyGuardDep])
    return app


async def test_validation_error_envelope():
    async with AsyncClient(transport=ASGITransport(app=_probe_app()), base_url="http://t") as c:
        r = await c.get("/api/x/v1/v?size=0", headers={"X-Request-ID": "rid-v"})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "VALIDATION_FAILED"
    assert body["details"]["fields"][0]["field"] == "size" and body["details"]["fields"][0]["message"]
    assert body["requestId"] == "rid-v"


async def test_unhandled_500_has_request_id():
    async with AsyncClient(
        transport=ASGITransport(app=_probe_app(), raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get("/api/x/v1/boom", headers={"X-Request-ID": "rid-500"})
    assert r.status_code == 500 and r.headers["x-request-id"] == "rid-500"
    assert r.json() == {
        "code": "INTERNAL_ERROR",
        "message": "服务器内部错误",
        "details": None,
        "requestId": "rid-500",
    }


async def test_global_readonly_guard_and_whitelist():
    async with AsyncClient(transport=ASGITransport(app=_probe_app()), base_url="http://t") as c:
        for m in ("post", "put", "patch", "delete"):
            assert (await c.request(m, "/api/x/v1/w")).status_code == 200
            r = await c.request(m, "/api/x/v1/w?ver=v2026.10.1")
            assert r.status_code == 403 and r.json()["code"] == "READONLY"
            assert r.json()["details"]["mode"] == "history"
            r = await c.request(m, "/api/x/v1/w?previewAs=何静")
            assert r.status_code == 403 and r.json()["details"]["mode"] == "preview"
        assert (await c.get("/api/x/v1/v?size=1&ver=v2026.10.1")).status_code == 200  # 读不受限
        assert (await c.post("/api/x/v1/rules/R-1:dry-run?ver=v2026.10.1")).status_code == 200
        assert (await c.post("/api/x/v1/permission-requests?previewAs=何静")).status_code == 200
        r = await c.post("/api/x/v1/w?ver=v2026.10.1&previewAs=何静")  # 两个都带 → preview
        assert r.status_code == 403 and r.json()["details"]["mode"] == "preview"
        assert (await c.post("/api/x/v1/history-only?ver=v2026.10.1")).status_code == 200
        r = await c.post("/api/x/v1/history-only?previewAs=何静")
        assert r.status_code == 403 and r.json()["details"]["mode"] == "preview"


async def test_every_mounted_write_route_is_guarded(monkeypatch):
    """遍历真实挂载（app.api.MOUNTS）里的所有写接口：带 ?ver= 一律 403（白名单除外）。"""
    from fastapi import APIRouter

    import app.api as api
    import app.common.context as view_context
    from app.main import create_app

    checked_modes = []
    policy = view_context.allowed_readonly_modes

    def record_modes(endpoint, route=None):
        modes = policy(endpoint, route)
        checked_modes.append(modes)
        return modes

    monkeypatch.setattr(view_context, "allowed_readonly_modes", record_modes)

    probe = APIRouter()

    @probe.post("/__probe")
    async def _p() -> dict:
        return {"ok": True}

    monkeypatch.setattr(api, "MOUNTS", [*api.MOUNTS, ("ontology", probe)])
    app = create_app()
    ops = [
        (m, path, item[m])
        for path, item in app.openapi()["paths"].items()
        for m in item
        if m.upper() not in ("GET", "HEAD", "OPTIONS")
    ]
    assert any(p == "/api/ontology/v1/__probe" for _, p, _ in ops)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for m, path, _op in ops:
            checked_modes.clear()
            url = path.replace("{", "").replace("}", "")
            r = await c.request(m, f"{url}?ver=v2026.10.1")
            assert checked_modes, (m, path, "mounted route did not invoke the readonly guard")
            if "history" in checked_modes[-1]:
                continue  # Both decorator and OpenAPI whitelist forms use the actual guard policy.
            assert r.status_code == 403 and r.json()["code"] == "READONLY", (m, path)


def _guard_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.post("/w", dependencies=[WritableDep])
    async def write() -> dict:
        return {"ok": True}

    return app


async def test_ver_and_preview_are_read_only():
    async with AsyncClient(transport=ASGITransport(app=_guard_app()), base_url="http://t") as c:
        assert (await c.post("/w")).json() == {"ok": True}
        r = await c.post("/w?ver=v2026.10.1")
        assert r.status_code == 403 and r.json()["code"] == "READONLY"
        assert r.json()["details"] == {"mode": "history", "ver": "v2026.10.1"}
        r = await c.post("/w?previewAs=何静")
        assert r.status_code == 403 and r.json()["code"] == "READONLY"
        assert r.json()["details"] == {"mode": "preview", "previewAs": "何静"}


async def test_openapi_has_module_prefixes(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert "/api/ontology/v1/releases" in paths
    assert (await client.get("/docs")).status_code == 200


async def test_integration_mounted_only_under_data(monkeypatch):
    """Palantir 定：integration 只在 /api/data/v1 下开路由，不挂 /api/ontology/v1。

    依据 action-event-api.md:140、:168。
    """
    from app import api
    from app.main import create_app
    from app.modules.integration import router as integration

    assert [g for g, r in api.MOUNTS if r is integration.router] == ["data"]
    monkeypatch.setattr(integration.router, "routes", list(integration.router.routes))

    async def _p() -> dict:
        return {"ok": True}

    integration.router.add_api_route("/ext-systems/__probe", _p, methods=["GET"])
    paths = create_app().openapi()["paths"]
    assert "/api/data/v1/ext-systems/__probe" in paths
    assert not [p for p in paths if p.endswith("/ext-systems/__probe") and not p.startswith("/api/data/v1/")]
