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


async def test_request_id_is_echoed_into_error_body(client):
    r = await client.get("/api/ontology/v1/releases/v1999.01.1", headers={"X-Request-ID": "rid-123"})
    assert r.status_code == 404
    assert r.headers["x-request-id"] == "rid-123"
    assert r.json() == {
        "code": "NOT_FOUND",
        "message": "版本不存在",
        "details": {"id": "v1999.01.1"},
        "requestId": "rid-123",
    }


async def test_validation_error_envelope(client):
    r = await client.get("/api/ontology/v1/releases?size=0")
    body = r.json()
    assert r.status_code == 422 and body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["loc"] == ["query", "size"]
    assert body["requestId"]


async def test_unknown_route_envelope(client):
    r = await client.get("/api/nope/v1/x")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


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
        assert r.status_code == 409 and r.json()["code"] == "READONLY_HISTORY"
        r = await c.post("/w?previewAs=何静")
        assert r.status_code == 409 and r.json()["code"] == "READONLY_PREVIEW"


async def test_openapi_has_module_prefixes(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert "/api/ontology/v1/releases" in paths
    assert (await client.get("/docs")).status_code == 200
