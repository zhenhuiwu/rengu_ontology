"""Preserved isolated retail workflow UI; all lifecycle state is written through real product APIs."""

import asyncio
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from scripts.model_smoke_budget import SmokeBudget, configured_runtime
from scripts.qa_use_test_server import BACKEND, DATABASE, application, configure

WORKSPACE = "wf-retail-20261027"
EMPTY_WORKSPACE = "wf-empty-20261027"
OWNER = "U010001"
REVIEWER = "U010002"
EMPTY_QUESTION = "当前空间有哪些可查询对象？"


async def empty_history_write(request, scope):
    """Only the fixed isolation question and this owner's local session history may be saved."""
    from sqlalchemy import func, select

    from app.common.ontology import set_ontology_scope
    from app.db.session import get_sessionmaker
    from app.modules.admin.models import SysUser
    from app.modules.qa.models import QaSession

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return False
    if not isinstance(body, dict):
        return False
    path, method = request.url.path, request.method
    async with get_sessionmaker()() as session:
        await set_ontology_scope(session, scope)
        owner = await session.scalar(select(SysUser.id).where(SysUser.code == OWNER))
        if path == "/api/qa/v1/sessions" and method == "POST":
            return (set(body) <= {"scope", "caliber"}
                    and await session.scalar(select(func.count()).select_from(QaSession).where(
                        QaSession.owner_id == owner)) == 0)
        match = re.fullmatch(r"/api/qa/v1/sessions/([^/:]+)(/turns(?::stream)?)?", path)
        if not match or not await session.scalar(select(QaSession.id).where(
                QaSession.code == match[1], QaSession.owner_id == owner)):
            return False
        if match[2] and method == "POST":
            return (set(body) <= {"question", "refs", "clientId"}
                    and body.get("question") == EMPTY_QUESTION and body.get("refs", []) == [])
        return (not match[2] and method == "PATCH" and set(body) == {"title"}
                and isinstance(body["title"], str) and 0 < len(body["title"].strip()) <= 60)


async def allowed_write(request):
    from sqlalchemy import select

    from app.db.session import get_sessionmaker
    from app.modules.ontology.models import OntologyWorkspace

    actor = request.headers.get("x-demo-as", OWNER)
    path, method = request.url.path, request.method
    if path == "/api/ontology/v1/workspaces" and method == "POST" and actor == OWNER:
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return False
        return isinstance(body, dict) and body.get("code") in (WORKSPACE, EMPTY_WORKSPACE)
    scope = request.headers.get("x-ontology-id", "")
    if not scope.isascii() or not scope.isdecimal():
        return False
    async with get_sessionmaker()() as session:
        owned = await session.scalar(select(OntologyWorkspace.id).where(
            OntologyWorkspace.id == int(scope), OntologyWorkspace.code == WORKSPACE,
        ))
        if owned is None and actor == OWNER:
            empty = await session.scalar(select(OntologyWorkspace.id).where(
                OntologyWorkspace.id == int(scope), OntologyWorkspace.code == EMPTY_WORKSPACE,
            ))
            if empty is not None:
                return await empty_history_write(request, empty)
    if owned is None:
        return False
    if (actor in (OWNER, REVIEWER) and method == "POST"
            and (path == "/api/ontology/v1/notifications:read-all"
                 or re.fullmatch(r"/api/ontology/v1/notifications/[1-9][0-9]*:read", path))):
        return True  # Product service still limits these marks to the current recipient.
    if actor == REVIEWER:
        return method == "POST" and re.fullmatch(r"/api/ontology/v1/change-requests/[^/:]+:(approve|reject)", path) is not None
    if actor != OWNER:
        return False
    # Search and scoped aggregation use POST for filters but never mutate business data.
    if method == "POST" and (re.fullmatch(r"/api/qa/v1/(object-types|objects)/(store|order)/search", path)
                             or path == "/api/qa/v1/scoped-ask"):
        return True
    if re.fullmatch(rf"/api/ontology/v1/workspaces/{owned}/members", path) and method == "PUT":
        body = await request.json()
        return isinstance(body, dict) and body.get("userId") == REVIEWER and body.get("role") == "editor"
    if path.startswith("/api/ontology/v1/change-requests"):
        return method in ("POST", "PUT", "PATCH")  # No delete/rollback cleanup of workflow evidence.
    if path in ("/api/ontology/v1/releases", "/api/ontology/v1/releases:merge-check"):
        return method == "POST"
    if re.fullmatch(r"/api/ontology/v1/metrics/[^/:]+:trial", path):
        return method == "POST"
    if path.startswith(("/api/ontology/v1/rules", "/api/ontology/v1/lifecycles", "/api/ontology/v1/actions/", "/api/ontology/v1/action-runs", "/api/ontology/v1/alerts/")):
        return method in ("POST", "PUT", "PATCH")
    if path.startswith("/api/data/v1/datasources/WF-RETAIL-SOURCE"):
        if path.endswith(("/import-preview", "/import-to-cr")):
            body = await request.json()
            return (isinstance(body, dict) and body.get("schema") == "public"
                    and set(body.get("tables", [])) <= {"store", "biz_order"}
                    and body.get("sampleValues", body.get("sample_values", False)) is False)
        return method == "POST" and path.endswith((":test", "/tables:refresh"))
    if path.startswith(("/api/qa/v1/pages", "/api/qa/v1/sessions")):
        return method in ("POST", "PUT", "PATCH")
    return False


async def verify(target):
    import asyncpg

    assert target.database == DATABASE
    conn = await asyncpg.connect(target.set(drivername="postgresql").render_as_string(hide_password=False))
    try:
        async with conn.transaction(readonly=True):
            assert await conn.fetchval("SELECT current_database()") == DATABASE
            version = await conn.fetchval("SELECT version_num FROM alembic_version")
            assert version == "dynamic_0030"
            return {"database": DATABASE, "version": version, "oldPages": await conn.fetchval("SELECT count(*) FROM qa_page"), "oldRuns": await conn.fetchval("SELECT count(*) FROM action_run")}
    finally:
        await conn.close()


def local_source(target):
    """Real introspection is confined to the same test DB, readonly role, and retail scope2."""
    import hmac

    from sqlalchemy import text

    from app.modules.data import connector
    from app.modules.qa.readonly import scope_readonly_conn
    from scripts.retail_workflow_prepare import local_settings

    local_settings(target)
    os.environ["RENGU_DS_REAL_CONNECT"] = "1"  # Only this controlled local profile, never the main service.

    async def read(kind, cfg, operation):
        if (kind != "PostgreSQL" or cfg.get("host") != target.host or cfg.get("port") != 5433
                or cfg.get("database") != DATABASE or cfg.get("user") != target.username
                or not hmac.compare_digest(cfg.get("password") or "", target.password or "")):
            from app.core.errors import AppError
            raise AppError("隔离内省仅允许已登记的本地零售副本", code="TEST_SOURCE_BLOCKED", status_code=403)
        engine = connector._engine(kind, cfg)
        try:
            async with engine.connect() as connection, connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                await scope_readonly_conn(connection, 2)
                result = await connection.run_sync(operation)
                if isinstance(result, list) and result and isinstance(result[0], connector.TableInfo):
                    result = [item for item in result if item.schema == "public" and item.name in ("store", "biz_order")]
                    for item in result:
                        item.rows = await connection.scalar(text(f'SELECT count(*) FROM public."{item.name}"'))
                return result
        finally:
            await engine.dispose()
    connector._read = read


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    target = configure()
    # This isolated process uses the actual client/business date; old fixture timestamps are unchanged.
    os.environ["RENGU_ENV"] = "dev"
    os.environ.pop("RENGU_FIXED_NOW", None)
    local_source(target)
    app = application(target, workflow_write=allowed_write)
    from fastapi import Request
    from fastapi.responses import RedirectResponse

    from app.core.errors import AppError
    from app.modules.qa.llm import get_llm

    @app.get("/api/_workflow/identity/{identity}", include_in_schema=False)
    async def workflow_identity(identity: str):
        if identity not in ("owner", "reviewer"):
            raise AppError("未知隔离验收身份", code="TEST_IDENTITY", status_code=404)
        result = RedirectResponse("/build/ontology/releases?ontology=2&cr=CR-0001", status_code=303)
        result.set_cookie("retail-workflow-actor", identity, httponly=True, samesite="strict")
        return result

    @app.middleware("http")
    async def workflow_actor(request: Request, call_next):
        # This test-only login chooses an actual stored identity; previewAs never grants approval.
        selected = request.cookies.get("retail-workflow-actor")
        if selected in ("owner", "reviewer"):
            headers = [(key, value) for key, value in request.scope["headers"] if key.lower() != b"x-demo-as"]
            headers.append((b"x-demo-as", (REVIEWER if selected == "reviewer" else OWNER).encode("ascii")))
            request.scope["headers"] = headers
        return await call_next(request)

    async def model_disabled():
        raise AppError("本轮模型预算尚未装载", code="MODEL_BUDGET_NOT_READY", status_code=503)

    app.dependency_overrides[get_llm] = model_disabled
    model_enabled = sys.argv[1:] == ["--enable-model"]
    if sys.argv[1:] and not model_enabled:
        raise RuntimeError("Only the explicit --enable-model switch is supported")
    if model_enabled:
        # ROOT fixes the ten questions after actual publication. No file means no model startup.
        directory = Path(os.environ["TEMP"]) / "rengu-ontology"
        questions = json.loads((directory / "retail-workflow-model-questions.json").read_text("utf-8"))
        if (not isinstance(questions, list) or len(questions) != 10
                or any(not isinstance(q, str) for q in questions) or questions[-1] != EMPTY_QUESTION):
            raise RuntimeError("Expected the fixed ten-question list")
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def budget_lifespan(instance):
            async with original_lifespan(instance):
                budget = SmokeBudget(await configured_runtime(), questions,
                                     directory / "model-smoke-20261027-budget.json")
                instance.dependency_overrides[get_llm] = budget.dependency
                try:
                    yield
                finally:
                    await budget.close()

        app.router.lifespan_context = budget_lifespan
    print(json.dumps({**asyncio.run(verify(target)), "workspaceCode": WORKSPACE, "owner": OWNER,
                      "reviewer": REVIEWER, "clock": "actual", "modelEnabled": model_enabled, "workers": 0}, ensure_ascii=False), flush=True)
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
