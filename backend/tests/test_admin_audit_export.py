"""真实提交的审计固定成员与 CSV 流；独立库避免只追加数据污染。"""

import asyncio
import csv
import io
import json
from datetime import UTC, datetime

import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.common.context import ReadonlyGuardDep
from app.core.errors import AppError, Forbidden, install_error_handlers
from app.db.session import get_session
from app.modules.admin import exports
from app.modules.admin.models import (
    AuditExportJob,
    AuditExportRow,
    AuditLog,
    AuditResult,
    SysRole,
    SysRoleMember,
    SysUser,
    UserStatus,
)
from app.modules.admin.router import _audit_view, router
from tests.helpers.migrations import db_state, temp_database

BASE = "/api/admin/v1"
BODY = {"from": "2026-10-01 00:00:00", "to": "2026-10-31 23:59:59"}
AT = datetime(2026, 10, 25, 1, tzinfo=UTC)


def audit(code, **kw):
    return AuditLog(
        code=code,
        time=AT,
        actor="王敏",
        action="查询",
        target=kw.pop("target", "订单"),
        ip="127.0.0.1",
        result=AuditResult.success,
        **kw,
    )


@pytest.fixture
async def audit_api():
    async with temp_database() as (cfg, _):
        await asyncio.to_thread(command.upgrade, cfg, "head")
        engine = create_async_engine(
            cfg.attributes["database_url"], pool_size=1, max_overflow=0, pool_timeout=0.5
        )
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(router, prefix=BASE, dependencies=[ReadonlyGuardDep])

        async def request_session():
            async with AsyncSession(engine, expire_on_commit=False) as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_session] = request_session
        async with AsyncSession(engine) as session:
            session.add_all(
                [
                    SysUser(code="U010001", name="王敏", status=UserStatus.active),
                    SysUser(code="U010006", name="李娜", status=UserStatus.active),
                ]
            )
            role = SysRole(
                code="R-AUDIT-TEST",
                name="测试审计查看",
                func={"audit": "view"},
                objs=[],
                changed_at=AT,
                changed_by="测试",
            )
            session.add(role)
            await session.flush()
            for user_id in await session.scalars(select(SysUser.id)):
                session.add(SysRoleMember(role_id=role.id, user_id=user_id, seq=0))
            await session.commit()
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                yield client, engine, app
        finally:
            await engine.dispose()


async def create(client, **filters):
    response = await client.post(BASE + "/audit-logs/export", json=BODY | filters)
    assert response.status_code == 200, response.text
    code = response.json()["jobId"]
    assert code.startswith("EXP-AL")
    return BASE + "/export-jobs/" + code


def csv_rows(response):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))


async def test_frozen_members_exclude_late_commit_lower_id_and_own_audit(audit_api, monkeypatch):
    client, engine, _ = audit_api
    slow_engine = create_async_engine(engine.url, poolclass=NullPool)
    try:
        async with AsyncSession(slow_engine) as slow:
            early = audit("EARLY")
            slow.add(early)
            await slow.flush()  # 较低 ID，但冻结成员时仍未提交。
            async with AsyncSession(engine) as setup:
                setup.add_all([audit("A"), audit("B"), audit("OTHER", target="会员")])
                await setup.commit()
            path = await create(client, field="target", kw="  订单  ", result="成功", action="查询")
            await slow.commit()
        async with AsyncSession(engine) as setup:
            setup.add(audit("LATER"))
            await setup.commit()
            job = await setup.scalar(select(AuditExportJob))
            assert job.filters["kw"] == "订单"
            assert await setup.scalar(select(func.count()).select_from(AuditExportRow)) == 2
        monkeypatch.setattr(exports, "BATCH_SIZE", 1)
        info = await client.get(path)
        assert info.json() == {
            "jobId": path.rsplit("/", 1)[1],
            "status": "ready",
            "rowCount": 2,
            "downloadUrl": path + "/download",
        }
        result = csv_rows(await client.get(path + "/download"))
        assert [r[0] for r in result[1:]] == ["B", "A"]  # time 相同，id 倒序。
        assert csv_rows(await client.get(path + "/download")) == result
    finally:
        await slow_engine.dispose()


async def test_csv_formula_multiline_empty_and_pool_release(audit_api, monkeypatch):
    client, engine, _ = audit_api
    values = ["=1+1", " +SUM(1,2)", "-2", "@SUM(A1)", "\ttext", "\rtext", '订单,"含引号"\n下一行']
    async with AsyncSession(engine) as setup:
        setup.add_all([audit(f"T{i}", target=v) for i, v in enumerate(values)])
        await setup.commit()
    monkeypatch.setattr(exports, "BATCH_SIZE", 2)
    original = exports.stream_csv

    async def checked_stream(*args):
        async for chunk in original(*args):
            assert engine.pool.checkedout() == 0  # 包括首批，单连接池无第二连接互等。
            yield chunk

    monkeypatch.setattr(exports, "stream_csv", checked_stream)
    path = await create(client)
    result = csv_rows(await client.get(path + "/download"))
    assert tuple(result[0]) == exports.HEADERS
    assert [row[4] for row in result[1:]] == list(reversed(["'" + v for v in values[:-1]] + [values[-1]]))
    empty = await create(client, kw="不存在")
    assert len(csv_rows(await client.get(empty + "/download"))) == 1
    assert (await client.get(empty)).json()["rowCount"] == 0


async def test_export_owner_current_permission_disabled_and_readonly(audit_api):
    client, engine, app = audit_api
    path = await create(client)
    for url in (path, path + "/download"):
        response = await client.get(url, headers={"X-Demo-As": "U010006"})
        assert response.status_code == 404
        assert (await client.get(url + "-missing")).status_code == 404
        for context in ({"ver": "v2026.10.1"}, {"previewAs": "U010006"}):
            response = await client.get(url, params=context)
            assert response.status_code == 403 and response.json()["code"] == "READONLY"

    async def denied():
        raise Forbidden()

    app.dependency_overrides[_audit_view] = denied
    for url in (path, path + "/download"):
        response = await client.get(url)
        assert response.status_code == 403 and response.json()["code"] == "NO_PERMISSION"
    assert (await client.post(BASE + "/audit-logs/export", json=BODY)).status_code == 403
    del app.dependency_overrides[_audit_view]
    async with AsyncSession(engine) as session:
        user = await session.scalar(select(SysUser).where(SysUser.code == "U010001"))
        user.status = UserStatus.disabled
        await session.commit()
    assert (await client.get(path + "/download")).status_code == 401


@pytest.mark.parametrize("field,value", [("kw", "x\x00"), ("action", "\ud800")])
async def test_invalid_export_text_does_not_write(audit_api, field, value):
    client, engine, _ = audit_api
    response = await client.post(
        BASE + "/audit-logs/export",
        content=json.dumps(BODY | {field: value}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_FAILED"
    async with AsyncSession(engine) as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditExportJob)) == 0
    assert (await client.get(BASE + "/export-jobs/bad%00id")).status_code == 422


async def test_first_batch_failure_is_json_error_before_csv_headers(audit_api, monkeypatch):
    client, _, _ = audit_api
    path = await create(client)

    async def failed(*args):
        raise AppError("读取失败", code="EXPORT_FAILED", status_code=503)

    monkeypatch.setattr(exports, "read_batch", failed)
    response = await client.get(path + "/download")
    assert response.status_code == 503 and response.json()["code"] == "EXPORT_FAILED"
    assert "content-disposition" not in response.headers


async def test_manifest_failure_rolls_back_job_members_and_audit(audit_api, monkeypatch):
    client, engine, _ = audit_api
    original = exports.create_manifest

    async def failed(*args):
        await original(*args)
        raise AppError("创建清单失败", code="EXPORT_FAILED", status_code=503)

    monkeypatch.setattr(exports, "create_manifest", failed)
    response = await client.post(BASE + "/audit-logs/export", json=BODY)
    assert response.status_code == 503
    async with AsyncSession(engine) as session:
        for model in (AuditLog, AuditExportJob, AuditExportRow):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_export_migration_metadata():
    async with temp_database() as (cfg, dsn):
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert {"audit_export_job", "audit_export_row"} <= (await db_state(dsn)).tables
        await asyncio.to_thread(command.check, cfg)


@pytest.mark.skip(
    reason="decisions-pending.md #10: head crosses forward-only notify_0022/admin_0025; "
    "metadata checked separately"
)
async def test_export_migration_roundtrip_and_metadata():
    async with temp_database() as (cfg, dsn):
        await asyncio.to_thread(command.upgrade, cfg, "dynamic_0004")
        before = await db_state(dsn)
        await asyncio.to_thread(command.upgrade, cfg, "head")
        at_head = await db_state(dsn)
        assert {"audit_export_job", "audit_export_row"} <= at_head.tables
        await asyncio.to_thread(command.check, cfg)
        await asyncio.to_thread(command.downgrade, cfg, "dynamic_0004")
        assert await db_state(dsn) == before
        await asyncio.to_thread(command.upgrade, cfg, "head")
        assert await db_state(dsn) == at_head
