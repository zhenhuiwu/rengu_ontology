"""Local-only systems are explicitly selected and fail closed outside their demo configuration."""

import json
import os
from unittest.mock import patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.common.ontology import set_ontology_scope
from app.common.pagination import PageParams
from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.integration import execution
from app.modules.integration.adapters import get_adapter, local_demo_config
from app.modules.integration.models import ExtSystem, ExtSystemStatus, WritebackCall
from app.modules.integration.schemas import ExtSystemIn
from app.modules.integration.service import ExtSystemService, is_writeback_target
from tests.test_integration_tls import https_receiver  # noqa: F401
from tests.test_palantir_webhook import SECRET, scope_of
from tests.test_use_ops_runtime import API, payload, runtime  # noqa: F401
from tests.use_ops_support import _no_external_http  # noqa: F401


def configure(monkeypatch, code, origin="https://127.0.0.1:4443", **overrides):
    settings = get_settings()
    target = {
        "ontologyId": 1,
        "systemCode": code,
        "origin": origin,
        "submitPath": "/submit",
        "queryPath": "/query",
        **overrides,
    }
    monkeypatch.setattr(settings, "env", "test")
    monkeypatch.setattr(settings, "integration_local_demo", json.dumps(target))
    monkeypatch.setattr(settings, "integration_real_connect", True)
    monkeypatch.setattr(
        settings,
        "integration_adapters",
        SecretStr(
            json.dumps(
                {
                    f"1/{code}": {
                        "submitPath": target["submitPath"],
                        "queryPath": target["queryPath"],
                        "actions": ["demo"],
                    }
                }
            )
        ),
    )
    return settings, target


def body(name, origin, **overrides):
    return ExtSystemIn.model_validate(
        {
            "name": name,
            "vendor": "local",
            "category": "Webhook",
            "endpoint": origin,
            "timeoutMs": 1000,
            "env": "测试",
            "idempotent": True,
            "idemKey": "Idempotency-Key",
            **overrides,
        }
    )


async def test_only_selected_new_system_is_marked_and_candidates_match(session, monkeypatch):
    service = ExtSystemService(session)
    legacy = await service.create(body("legacy test", "https://old.example", localOnly=True), "test")
    prod = await service.create(body("normal production", "https://prod.example", env="生产"), "test")
    code = await service._next_code()
    settings, target = configure(monkeypatch, code)
    local = await service.create(body("selected local", target["origin"], localOnly=False), "test")
    assert not legacy.local_only and not prod.local_only and local.local_only
    assert is_writeback_target(prod) and is_writeback_target(local) and not is_writeback_target(legacy)
    rows, count = await service.page(writeback=True, kw=None, page=PageParams(page=1, size=1))
    assert count == 2 and [r.code for r in rows] == [prod.code]
    rows, count = await service.page(writeback=True, kw=None, page=PageParams(page=2, size=1))
    assert count == 2 and [r.code for r in rows] == [local.code]
    await service.update(
        local.code, body("selected local renamed", target["origin"], localOnly=False), "test"
    )
    assert (await session.scalar(select(ExtSystem).where(ExtSystem.code == local.code))).local_only
    with pytest.raises(AppError) as error:
        await service.update(local.code, body("selected local renamed", target["origin"], env="生产"), "test")
    assert error.value.code == "LOCAL_DEMO_IMMUTABLE"
    monkeypatch.setattr(settings, "integration_local_demo", None)
    assert not is_writeback_target(local) and is_writeback_target(prod)
    assert [r.code for r in await service.search(writeback=True, kw=None)] == [prod.code]
    for operation in ("submit", "query", "probe", "effect"):
        with pytest.raises(AppError):
            get_adapter(local, operation)


async def test_selected_system_cannot_be_created_when_demo_is_disabled_or_prod(session, monkeypatch):
    service = ExtSystemService(session)
    code = await service._next_code()
    settings, target = configure(monkeypatch, code)
    monkeypatch.setattr(settings, "env", "prod")
    with pytest.raises(AppError) as error:
        await service.create(body("disabled demo", target["origin"]), "test")
    assert error.value.code == "LOCAL_DEMO_INVALID"
    assert await session.scalar(select(ExtSystem).where(ExtSystem.code == code)) is None
    monkeypatch.setattr(settings, "env", "test")
    with pytest.raises(AppError) as error:
        await service.create(body("wrong target", "https://other.example"), "test")
    assert error.value.code == "LOCAL_DEMO_INVALID"
    monkeypatch.setattr(settings, "integration_real_connect", False)
    with pytest.raises(AppError) as error:
        await service.create(body("disabled demo", target["origin"]), "test")
    assert error.value.code == "LOCAL_DEMO_INVALID"
    assert await session.scalar(select(ExtSystem).where(ExtSystem.code == code)) is None


async def test_committed_mark_rejects_changed_db_target_in_new_session(engine, monkeypatch):
    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    system_id = None
    try:
        async with factory() as session:
            await set_ontology_scope(session, 1)
            service = ExtSystemService(session)
            code = await service._next_code()
            settings, target = configure(monkeypatch, code)
            system = await service.create(body("persisted local", target["origin"]), "test")
            await session.commit()
            system_id = system.id
            adapter = get_adapter(system, "query")
            monkeypatch.setattr(settings, "integration_local_demo", None)
            with pytest.raises(AppError):
                await adapter.request("query", "never-sent")
            with pytest.raises(AppError):
                get_adapter(system, "submit")
            monkeypatch.setattr(settings, "integration_local_demo", json.dumps(target))
        for changes in (
            {"env": "生产"},
            {"endpoint": "https://other.example"},
            {"status": ExtSystemStatus.disabled},
        ):
            async with engine.begin() as conn:
                stmt = update(ExtSystem).where(ExtSystem.ontology_id == 1, ExtSystem.id == system_id)
                await conn.execute(stmt.values(**changes))
            async with factory() as check:
                await set_ontology_scope(check, 1)
                system = await check.scalar(select(ExtSystem).where(ExtSystem.code == code))
                assert system.local_only and not is_writeback_target(system)
                for operation in ("submit", "query", "probe", "effect"):
                    with pytest.raises(AppError):
                        get_adapter(system, operation)
            async with engine.begin() as conn:
                stmt = update(ExtSystem).where(ExtSystem.ontology_id == 1, ExtSystem.id == system_id)
                await conn.execute(
                    stmt.values(env="测试", endpoint=target["origin"], status=ExtSystemStatus.ok)
                )
    finally:
        if system_id is not None:
            async with engine.begin() as conn:
                stmt = ExtSystem.__table__.delete().where(
                    ExtSystem.ontology_id == 1, ExtSystem.id == system_id
                )
                await conn.execute(stmt)


async def test_local_adapter_checks_every_operation_and_real_loopback_tls(
    session,
    monkeypatch,
    https_receiver,  # noqa: F811
    request,
):
    code = await ExtSystemService(session)._next_code()
    origin = https_receiver.url.removesuffix("/hook")
    settings, target = configure(monkeypatch, code, origin, submitPath="/hook", queryPath="/hook")
    monkeypatch.setattr(settings, "integration_ca_file", https_receiver.ca_file)
    system = await ExtSystemService(session).create(body("tls local", origin), "test")
    adapter = get_adapter(system, "query")
    request.node.local_webhook_endpoint = https_receiver.url + "?idemKey=known-key"
    assert (await adapter.request("query", "known-key")).status_code == 200
    assert len(https_receiver.requests) == 1 and adapter.verify.check_hostname
    for operation in ("probe", "effect"):
        with pytest.raises(AppError):
            get_adapter(system, operation)
    with pytest.raises(AppError):
        adapter.path("probe")
    for attr, value in (
        ("env", "生产"),
        ("category", "HTTP"),
        ("endpoint", "https://other.example"),
        ("status", ExtSystemStatus.disabled),
        ("ontology_id", 2),
        ("code", "EXT-OTHER"),
    ):
        before = getattr(system, attr)
        setattr(system, attr, value)
        assert not is_writeback_target(system)
        with pytest.raises(AppError):
            get_adapter(system, "submit")
        setattr(system, attr, before)
    monkeypatch.setattr(settings, "integration_local_demo", json.dumps({**target, "submitPath": "/changed"}))
    with pytest.raises(AppError):
        adapter.path("query")
    assert len(https_receiver.requests) == 1
    monkeypatch.setattr(settings, "integration_local_demo", json.dumps(target))
    monkeypatch.setattr(settings, "env", "prod")
    assert not is_writeback_target(system)
    with pytest.raises(AppError):
        get_adapter(system, "submit")


async def test_marked_system_dispatches_signed_local_action(runtime, client, monkeypatch, https_receiver):  # noqa: F811
    session, action, _ = runtime
    service = ExtSystemService(session)
    code = await service._next_code()
    origin = https_receiver.url.removesuffix("/hook")
    settings, _ = configure(monkeypatch, code, origin, submitPath="/hook", queryPath="/hook")
    monkeypatch.setattr(settings, "integration_ca_file", https_receiver.ca_file)
    monkeypatch.setattr(
        settings,
        "integration_adapters",
        SecretStr(
            json.dumps(
                {
                    f"1/{code}": {
                        "submitPath": "/hook",
                        "queryPath": "/hook",
                        "actions": [action.code],
                        "signingSecret": SECRET,
                    }
                }
            )
        ),
    )
    system = await service.create(body("signed local", origin), "test")
    action.sys_id = system.id
    await session.flush()
    staged = await client.post(f"{API}/action-runs:stage", json=payload())
    assert staged.status_code == 201, staged.text
    action_code = staged.json()["id"]
    confirmed = await client.post(f"{API}/action-runs/{action_code}:confirm")
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "结果未知"
    call = await session.scalar(select(WritebackCall).where(WritebackCall.msg == execution.QUEUED))
    with patch.object(execution, "scoped_sessions", scope_of(session)):
        await execution.dispatch_one(call.id, 1)
    await session.refresh(call)
    assert call.http_status == 200 and call.attempt == 1 and len(https_receiver.requests) == 1
    final = await client.get(f"{API}/action-runs/{action_code}")
    assert final.json()["status"] == "已对账" and final.json()["extRef"] == "TLS-RECEIPT"


@pytest.mark.parametrize(
    "origin",
    [
        "https://localhost:4443",
        "https://127.0.0.2:4443",
        "http://127.0.0.1:4443",
        "https://127.0.0.1",
        "https://127.0.0.1:4443/path",
        "https://user@127.0.0.1:4443",
    ],
)
def test_only_literal_loopback_origin_is_accepted(monkeypatch, origin):
    configure(monkeypatch, "EXT-N01", origin)
    assert local_demo_config() is None


@pytest.mark.parametrize("path", ["/a/../b", "/a/./b", "//other", "/a%2fb", "/a?x=1", "/a\\b"])
def test_only_canonical_local_paths_are_accepted(monkeypatch, path):
    configure(monkeypatch, "EXT-N01", submitPath=path)
    assert local_demo_config() is None


def test_local_demo_is_disabled_in_production(monkeypatch):
    settings, _ = configure(monkeypatch, "EXT-N01")
    monkeypatch.setattr(settings, "env", "prod")
    assert local_demo_config() is None
