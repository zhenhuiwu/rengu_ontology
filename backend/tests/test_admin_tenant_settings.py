"""租户基础信息扩展：真实读写、乐观版本、行锁及审计原子性。"""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.admin.models import AuditLog, SysOrg, SysTenant
from app.modules.admin.tenant_settings import TenantSettingsIn, update_tenant
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/admin/v1/settings/tenant"
AUTHOR = {"X-Demo-As": "U010001", "X-Forwarded-For": "10.1.2.3"}


@pytest.fixture
async def tenant(session):
    await run_seeds(session, ["admin"])
    await grant_fixture_roles(session)
    await session.commit()
    return session


async def state(client):
    result = await client.get(P, headers=AUTHOR)
    assert result.status_code == 200, result.text
    return result.json()


def body(data, **changes):
    return {**{k: data[k] for k in ("name", "shortName", "defaultOrg", "lockVersion")}, **changes}


async def audit_count(session):
    return int(await session.scalar(select(func.count()).select_from(AuditLog)))


async def test_get_put_updates_only_supported_fields_and_real_audit(client, tenant):
    old = await state(client)
    assert old["tenantId"] == "TN-XL-000317" and old["industry"]
    assert old["orgOptions"] == [
        {"value": "hd", "label": "华东事业部"},
        {"value": "hn", "label": "华南事业部"},
        {"value": "hq", "label": "集团总部"},
    ]
    count = await audit_count(tenant)
    result = await client.put(
        P, json=body(old, name=" 新租户 ", shortName=" 新简称 ", defaultOrg="hn"), headers=AUTHOR
    )
    assert result.status_code == 200, result.text
    saved = result.json()
    assert (saved["name"], saved["shortName"], saved["defaultOrg"], saved["lockVersion"]) == (
        "新租户",
        "新简称",
        "hn",
        old["lockVersion"] + 1,
    )
    assert saved["tenantId"] == old["tenantId"] and saved["industry"] == old["industry"]
    assert saved == await state(client)
    options = (await client.get("/api/admin/v1/tenants", headers=AUTHOR)).json()
    assert all(o["label"].startswith("新简称 / ") and o["tenantName"] == "新租户" for o in options)
    assert [o["value"] for o in options if o["default"]] == ["hn"]
    assert await audit_count(tenant) == count + 1
    audit = await tenant.scalar(select(AuditLog).where(AuditLog.action == "修改租户基础信息"))
    assert audit.actor == "王敏（U010001）" and audit.ip == "10.1.2.3" and audit.created_by == "U010001"
    assert "新租户 / 新简称" in audit.target and "hn（华南事业部）" in audit.target


async def test_default_only_advances_parent_once_and_stale_editor_preserved(client, tenant):
    old = await state(client)
    result = await client.put(P, json=body(old, defaultOrg="hq"), headers=AUTHOR)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["lockVersion"] == old["lockVersion"] + 1 and saved["name"] == old["name"]
    count = await audit_count(tenant)
    result = await client.put(P, json=body(old, name="旧编辑者"), headers=AUTHOR)
    assert result.status_code == 409 and result.json()["code"] == "VERSION_CONFLICT", result.text
    assert await state(client) == saved and await audit_count(tenant) == count


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "x" * 41},
        {"name": "  "},
        {"name": 123},
        {"shortName": "x" * 13},
        {"shortName": "\0"},
        {"name": "\ud800"},
        {"defaultOrg": "华东事业部"},
        {"defaultOrg": "missing"},
        {"defaultOrg": "hd\0"},
        {"lockVersion": 0},
        {"lockVersion": True},
        {"lockVersion": None},
        {"tenantId": "other"},
        {"industry": "other"},
        {"timezone": "UTC"},
        {"security": {}},
    ],
)
async def test_invalid_save_changes_nothing(client, tenant, changes):
    old = await state(client)
    count = await audit_count(tenant)
    result = await client.put(
        P, content=json.dumps(body(old, **changes)), headers={**AUTHOR, "Content-Type": "application/json"}
    )
    assert result.status_code == 422 and result.json()["code"] == "VALIDATION_FAILED", result.text
    assert await state(client) == old and await audit_count(tenant) == count


@pytest.mark.parametrize("query", ["ver=v2026.10.1", "previewAs=U010004", "ver=v1&previewAs=U010004"])
async def test_readonly_cannot_save(client, tenant, query):
    old = await state(client)
    result = await client.put(f"{P}?{query}", json=body(old, name="不应保存"), headers=AUTHOR)
    assert result.status_code == 403 and result.json()["code"] == "READONLY", result.text
    assert await state(client) == old


async def test_audit_failure_rolls_back_tenant_and_orgs(client, tenant, monkeypatch):
    from app.modules.admin import tenant_settings

    old = await state(client)
    count = await audit_count(tenant)

    async def fail(*args, **kwargs):
        raise AppError("审计不可用", code="AUDIT_UNAVAILABLE", status_code=503)

    monkeypatch.setattr(tenant_settings, "insert_audit", fail)
    result = await client.put(P, json=body(old, name="应回滚", defaultOrg="hq"), headers=AUTHOR)
    assert result.status_code == 503, result.text
    assert await state(client) == old and await audit_count(tenant) == count
    assert list(await tenant.scalars(select(SysOrg.code).where(SysOrg.is_default))) == [old["defaultOrg"]]


async def test_missing_tenant_is_explicit_404(client, users):
    await grant_fixture_roles(users)
    await users.execute(delete(SysTenant))
    result = await client.get(P, headers=AUTHOR)
    assert result.status_code == 404 and result.json()["code"] == "NOT_FOUND"


async def test_second_transaction_waits_for_parent_row_lock(engine):
    """真实连接验证等待；更新及审计均回滚，测试不遗留只追加日志。"""
    token = uuid4().hex[:10]
    created_tenant = None
    codes = [f"org-{token}-1", f"org-{token}-2"]
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        parent = await setup.scalar(select(SysTenant).order_by(SysTenant.id).limit(1))
        if parent is None:
            parent = SysTenant(code=f"tenant-{token}", name="并发租户", short_name="并发")
            setup.add(parent)
            await setup.flush()
            created_tenant = parent.id
        setup.add_all([SysOrg(code=c, name=c, seq=100 + i) for i, c in enumerate(codes)])
        await setup.commit()
        parent_id, version, name, short = parent.id, parent.lock_version, parent.name, parent.short_name
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as first,
            AsyncSession(engine, expire_on_commit=False) as second,
        ):
            await first.scalar(select(SysTenant).where(SysTenant.id == parent_id).with_for_update())
            pid = await second.scalar(text("SELECT pg_backend_pid()"))
            await second.rollback()
            task = asyncio.create_task(
                update_tenant(
                    second,
                    TenantSettingsIn(
                        name=name[:40],
                        shortName=short[:12],
                        defaultOrg=codes[1],
                        lockVersion=version,
                    ),
                    CurrentUser(id="U-LOCK", name="并发校验"),
                    "127.0.0.1",
                )
            )
            try:
                async with asyncio.timeout(5):
                    while not await first.scalar(
                        text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"),
                        {"pid": pid},
                    ):
                        if task.done():
                            await task
                            pytest.fail("更新未等待父租户行锁")
                assert not task.done()
                await first.rollback()
                result = await task
                assert result.lock_version == version + 1 and result.default_org == codes[1]
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await first.rollback()
                await second.rollback()
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(delete(SysOrg).where(SysOrg.code.in_(codes)))
            if created_tenant is not None:
                await cleanup.execute(delete(SysTenant).where(SysTenant.id == created_tenant))
            await cleanup.commit()
