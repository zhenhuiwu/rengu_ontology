"""Dedicated QA UI server with exact local test profiles; no model or external dispatch."""

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
DATABASE = "rengu_test_use_qa_20261027"


def configure():
    source = make_url(dotenv_values(BACKEND / ".env").get("RENGU_DATABASE_URL") or "")
    if (
        source.host not in ("localhost", "127.0.0.1")
        or source.port != 5433
        or source.database != "rengu_ontology"
    ):
        raise RuntimeError("Unexpected database origin; test server refused")
    target = source.set(database=DATABASE)
    os.environ.update(
        RENGU_DATABASE_URL=target.render_as_string(hide_password=False),
        RENGU_QA_READONLY_DATABASE_URL=target.render_as_string(hide_password=False),
        RENGU_ENV="test",
        DASHSCOPE_API_KEY="",
        OPENAI_API_KEY="",
        AUTO_ONTOLOGY_LLM_API_KEY="",
        RENGU_INTEGRATION_REAL_CONNECT="false",
        RENGU_EVENT_DELIVERY_HTTP_ENABLED="false",
        RENGU_DS_REAL_CONNECT="0",
        RENGU_OBJECT_READ_ENABLED="0",
        RENGU_OBJECT_READ_BINDINGS="",
    )
    return target


def application(target, *, fixes_profile=False):
    from fastapi.responses import JSONResponse
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    import app.db.session as database
    from app.main import app

    readonly = create_async_engine(
        target,
        poolclass=NullPool,
        execution_options={"postgresql_readonly": True},
        **database.qa_readonly_engine_kwargs(15000),
    )

    @event.listens_for(readonly.sync_engine, "begin")
    def downgrade(connection):
        connection.exec_driver_sql("SET LOCAL ROLE rengu_ontology_scoped")

    database._qa_engine = readonly

    if fixes_profile:
        import httpx

        def denied_sync(*args, **kwargs):
            raise RuntimeError("External HTTP is disabled in the isolated UI profile")

        async def denied_async(*args, **kwargs):
            denied_sync()

        httpx.HTTPTransport.handle_request = denied_sync
        httpx.AsyncHTTPTransport.handle_async_request = denied_async

    async def fixes_write(request):
        if (
            request.headers.get("x-ontology-id") != "1"
            or request.headers.get("x-demo-as", "U010001") != "U010001"
        ):
            return False
        path = request.url.path
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return False
        if request.method == "PUT" and path == "/api/qa/v1/pages/UICHECK-FIX-PAGE/schedule":
            if not isinstance(body, dict) or set(body) != {"refresh", "sub"}:
                return False
            plan, sub = body["refresh"], body["sub"]
            if (
                not isinstance(plan, dict)
                or not isinstance(sub, dict)
                or set(plan) - {"freq", "time", "onFail"}
                or set(sub) - {"on", "channel", "receivers", "when", "onlyChanged"}
                or plan.get("freq") not in ("手动", "每天")
                or plan.get("onFail") != "保留上次结果"
                or sub.get("channel") != "站内信"
                or type(sub.get("on")) is not bool
                or not isinstance(sub.get("receivers"), list)
                or any(name != "王敏" for name in sub["receivers"])
                or sub.get("when") not in ("每次刷新后", "每天 09:00", "每周一 09:00")
            ):
                return False
            return (plan["freq"] == "手动" and plan.get("time") == "--") or (
                plan["freq"] == "每天"
                and isinstance(plan.get("time"), str)
                and re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", plan["time"]) is not None
            )
        if request.method == "PATCH" and path == "/api/admin/v1/api-keys/AK-UICHECK-FIX":
            return (
                isinstance(body, dict)
                and set(body)
                <= {"scopes", "actionTypes", "eventTypes", "functionTypes", "ratePerMinute", "quotaPerDay"}
                and set(body.get("scopes", [])) <= {"meta:read", "functions:invoke"}
                and body.get("actionTypes") is None
                and body.get("eventTypes") is None
                and isinstance(body.get("functionTypes"), list)
                and set(body["functionTypes"]) <= {"UICHECK-FIX-FN-A", "UICHECK-FIX-FN-B"}
            )
        if request.method != "POST":
            return False
        from sqlalchemy import select

        from app.common.ontology import set_ontology_scope

        match = re.fullmatch(r"/api/ontology/v1/notifications/([0-9]+):read", path)
        if match and body == {}:
            from app.modules.admin.models import SysUser
            from app.modules.notifications.models import Notification

            async with database.get_sessionmaker()() as session:
                await set_ontology_scope(session, 1)
                return bool(
                    await session.scalar(
                        select(Notification.id)
                        .join(SysUser, SysUser.id == Notification.recipient_id)
                        .where(
                            Notification.id == int(match[1]),
                            Notification.source_kind == "page",
                            Notification.target_code == "UICHECK-FIX-PAGE",
                            SysUser.code == "U010001",
                        )
                    )
                )
        match = re.fullmatch(r"/api/data/v1/action-runs/UICHECK-FIX-RUN/effects/([0-9]+):retry", path)
        if (
            match
            and isinstance(body, dict)
            and set(body) == {"attempts", "state"}
            and body["state"] == "failed"
        ):
            from app.common.outbox import OutboxEvent

            async with database.get_sessionmaker()() as session:
                await set_ontology_scope(session, 1)
                return bool(
                    await session.scalar(
                        select(OutboxEvent.id).where(
                            OutboxEvent.id == int(match[1]),
                            OutboxEvent.event_id == "UICHECK-FIX:effect:failed",
                            OutboxEvent.payload["ontologyId"].as_string() == "1",
                        )
                    )
                )
        return False

    async def local_metric_trial(request):
        if (
            request.method != "POST"
            or request.url.path != "/api/ontology/v1/metrics/uicheck_order_paid:trial"
            or request.headers.get("x-ontology-id") != "1"
            or request.headers.get("x-demo-as", "U010001") != "U010001"
        ):
            return False
        try:
            return await request.json() == {"range": "30d"}
        except (ValueError, TypeError):
            return False

    async def local_action_write(request):
        if (
            request.method != "POST"
            or request.headers.get("x-ontology-id") != "1"
            or request.headers.get("x-demo-as", "U010001") != "U010001"
        ):
            return False
        path = request.url.path
        if path in ("/api/ontology/v1/actions/ops_local:validate", "/api/ontology/v1/action-runs:stage"):
            try:
                body = await request.json()
                if not isinstance(body, dict):
                    return False
                params = (
                    json.loads(body["params"]) if isinstance(body.get("params"), str) else body.get("params")
                )
            except (ValueError, KeyError, TypeError):
                return False
            if (
                not isinstance(body, dict)
                or body.get("objKind") != "store"
                or body.get("objId") != "OPS-UI-LOCAL"
            ):
                return False
            if path.endswith(":validate"):
                return isinstance(params, dict)
            if (
                not isinstance(params, dict)
                or set(params) != {"enabled"}
                or type(params["enabled"]) is not bool
            ):
                return False
            return body.get("typeId") == "ops_local" and body.get("source") == {"type": "手动"}
        match = re.fullmatch(r"/api/ontology/v1/action-runs/([^/]+):confirm", path)
        if match:
            from sqlalchemy import select

            from app.common.ontology import set_ontology_scope
            from app.modules.dynamic.models import ActionRun

            async with database.get_sessionmaker()() as session:
                await set_ontology_scope(session, 1)
                return bool(
                    await session.scalar(
                        select(ActionRun.id).where(
                            ActionRun.code == match[1],
                            ActionRun.action_type_code == "ops_local",
                            ActionRun.obj_kind == "store",
                            ActionRun.obj_id == "OPS-UI-LOCAL",
                            ActionRun.sys_code.is_(None),
                            ActionRun.is_seed.is_(False),
                        )
                    )
                )
        return False

    @app.middleware("http")
    async def page_writes_only(request, call_next):
        if fixes_profile and request.method not in ("GET", "HEAD", "OPTIONS"):
            if not await fixes_write(request):
                return JSONResponse(
                    status_code=403,
                    content={"code": "TEST_SIDE_EFFECT_BLOCKED", "message": "隔离验证仅允许指定测试记录操作"},
                )
            return await call_next(request)
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            and not (
                request.url.path == "/api/qa/v1/pages" or request.url.path.startswith("/api/qa/v1/pages/")
            )
            and not await local_action_write(request)
            and not await local_metric_trial(request)
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "code": "TEST_SIDE_EFFECT_BLOCKED",
                    "message": "隔离测试服务仅允许页面、指定本地动作及指标试算；模型和外部执行关闭",
                },
            )
        return await call_next(request)

    return app


async def prepare(target):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.common.auth import resolve_user
    from app.common.ontology import set_ontology_scope
    from app.core import clock
    from app.modules.admin.models import SysUser
    from app.modules.qa import page_execution as replay
    from app.modules.qa.models import CardViz, QaSession, QaTurn, TurnKind, TurnStatus
    from app.modules.versioning.repo import ReleaseRepo
    from tests.qa_page_test_support import committed_qa

    engine = create_async_engine(target, poolclass=NullPool)
    try:
        await committed_qa.__wrapped__(engine)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await set_ontology_scope(session, 1)
            from scripts.qa_test_ops_prepare import prepare_ops

            await prepare_ops(session)
            user = await resolve_user(session, "U010001")
            existing = await session.scalar(select(QaSession.id).where(QaSession.code == "QA-UI-SOURCE"))
            if existing:
                await session.commit()
                return
            release = await ReleaseRepo(session).latest()
            sql = "SELECT store_no, name FROM store ORDER BY store_no"
            plan = replay.saved_plan(sql, release)
            card = SimpleNamespace(viz=CardViz.table, sem_plan={"execution": plan}, pending_cr=None)
            caches, _ = await replay.execute_cards(session, user, [card], release=release)
            query = QaSession(
                code="QA-UI-SOURCE",
                title="隔离测试库：真实门店只读源",
                owner_id=await session.scalar(select(SysUser.id).where(SysUser.code == user.id)),
                last_at=clock.now(),
                scope="原始查询条件",
            )
            session.add(query)
            await session.flush()
            cache = caches[0]
            session.add(
                QaTurn(
                    code="QA-UI-SOURCE-T001",
                    session_id=query.id,
                    seq=1,
                    kind=TurnKind.answer,
                    status=TurnStatus.done,
                    question="隔离测试库门店目录",
                    sql_text=sql,
                    release_id=release.id,
                    answered_ver=release.ver,
                    sem_plan={"execution": plan},
                    asked_at=clock.now(),
                    snapshot={
                        "answer": {
                            "card": "table",
                            "text": "以下为隔离测试库当前可见门店。",
                            "table": {"columns": cache["columns"], "items": cache["items"]},
                        }
                    },
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    target = configure()
    app = application(target)
    asyncio.run(prepare(target))
    print(
        f"QA test UI ready: database={DATABASE}; source=QA-UI-SOURCE; model/external writes blocked",
        flush=True,
    )
    if "--prepare-only" not in sys.argv:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
