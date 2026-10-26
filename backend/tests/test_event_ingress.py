"""Authenticated, atomic event intake without a fabricated delivery executor."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import get_settings
from app.modules.action.models import TypeStatus
from app.modules.event.ingress import receive
from app.modules.event.ingress_schema import IngressIn
from app.modules.event.models import (
    Event,
    EventDedup,
    EventOutcome,
    EventPayloadField,
    EventSource,
    EventType,
)
from app.modules.versioning.service import VersioningService
from tests.function_access_support import grant_fixture_roles

URL = "/internal/v1/events"
ONT = "/api/ontology/v1"
AUTH = {"Authorization": "Bearer isolated-test-token"}


def body(**changes):
    return {
        "eventId": "producer-1",
        "eventType": "ingress_test",
        "occurredAt": clock.now().isoformat(),
        "source": "producer:orders",
        "objectType": "SalesOrder",
        "objectId": "SO-actual",
        "payload": {"amount": 4},
        "dedupKey": "order:SO-actual:status",
        "traceId": "trace-actual",
        **changes,
    }


def event_type(code="ingress_test"):
    return EventType(
        code=code,
        api=f"Api_{code}",
        name="真实入站类型",
        source=EventSource.external,
        object="订单",
        object_api="SalesOrder",
        obj_kind="order",
        status=TypeStatus.published,
        version="v1",
        dedup_key_rule="事件 ID",
        dedup_window="1 小时",
        retention_days=30,
    )


@pytest.fixture
async def ingress(users, monkeypatch):
    monkeypatch.setattr(get_settings(), "internal_event_token", SecretStr("isolated-test-token"))
    await grant_fixture_roles(users, {"U010001": "R-01"})
    et = event_type()
    users.add(et)
    await users.flush()
    users.add(
        EventPayloadField(event_type_id=et.id, seq=1, name="金额", api="amount", type="整数", required=True)
    )
    await users.flush()
    return users, et


async def test_event_ingress_auth_is_independent_and_readonly(ingress, client, monkeypatch):
    session, _ = ingress
    for headers in ({}, {"Authorization": "Bearer wrong", "X-Demo-As": "U010001"}):
        response = await client.post(URL, json=body(), headers=headers)
        assert response.status_code == 401
    monkeypatch.setattr(get_settings(), "internal_event_token", None)
    response = await client.post(URL, json=body(), headers=AUTH)
    assert response.status_code == 503 and response.json()["code"] == "EVENT_INGRESS_UNAVAILABLE"
    monkeypatch.setattr(get_settings(), "internal_event_token", SecretStr("isolated-test-token"))
    for query in ("?ver=v2026.10.1", "?previewAs=U010001"):
        response = await client.post(URL + query, json=body(), headers=AUTH)
        assert response.status_code == 403 and response.json()["code"] == "READONLY"
    assert await session.scalar(select(func.count()).select_from(Event)) == 0
    # An invalid demo user cannot block or authorize a service credential.
    response = await client.post(URL, json=body(), headers={**AUTH, "X-Demo-As": "NOT-A-USER"})
    assert response.status_code == 200 and response.json() == {"eventId": "producer-1", "deduped": False}


async def test_event_ingress_persists_envelope_pending_version_and_replay(ingress, client):
    session, et = ingress
    ver = await VersioningService().create_version(
        session, ["ingress-test-config"], published_by="U010001", note="测试接收版本"
    )
    response = await client.post(URL, json=body(), headers=AUTH)
    assert response.status_code == 200, response.text
    event = await session.scalar(select(Event).where(Event.code == "producer-1"))
    assert event.payload == {"amount": 4} and event.source == "producer:orders"
    assert event.producer_type == "ingress_test" and event.producer_object_type == "SalesOrder"
    assert event.trace_id == "trace-actual" and event.ingested_at == clock.now()
    assert event.outcome == EventOutcome.pending and event.subscriber_count == 0 and event.latency_ms is None
    assert event.release_id is not None and event.envelope_hash
    record = (await client.get(f"{ONT}/events/producer-1")).json()
    assert record["outcome"] == "待投递" and record["latencyMs"] is None and record["ontologyVersion"] == ver
    assert record["source"] == "producer:orders" and record["traceId"] == "trace-actual"
    stats = (await client.get(f"{ONT}/event-types/{et.code}/stats")).json()
    assert stats["total"] == 1 and stats["pending"] == 1 and stats["failed"] == 0 and stats["p95"] is None
    response = await client.post(URL, json=body(), headers=AUTH)
    assert response.json() == {"eventId": "producer-1", "deduped": False}
    for changed in ({"payload": {"amount": 5}}, {"traceId": "other"}, {"source": "other"}):
        response = await client.post(URL, json=body(**changed), headers=AUTH)
        assert response.status_code == 409 and response.json()["code"] == "EVENT_ID_CONFLICT"
    assert await session.scalar(select(func.count()).select_from(Event)) == 1


async def test_event_ingress_business_window_and_permanent_dedup(ingress, client):
    session, et = ingress
    assert (await client.post(URL, json=body(), headers=AUTH)).json()["deduped"] is False
    response = await client.post(URL, json=body(eventId="producer-2"), headers=AUTH)
    assert response.json()["deduped"] is True
    event = await session.scalar(select(Event).where(Event.code == "producer-2"))
    assert event.outcome == EventOutcome.deduped and event.subscriber_count == 0 and event.latency_ms is None
    state = await session.scalar(select(EventDedup))
    assert state.last_accepted_at == clock.now(), "deduped requests must not extend the original window"
    state.last_accepted_at = clock.now() - timedelta(hours=1)
    await session.flush()
    assert (await client.post(URL, json=body(eventId="producer-3"), headers=AUTH)).json()["deduped"] is False
    et.dedup_window = "永久（不重复）"
    state.last_accepted_at = clock.now() - timedelta(days=5000)
    await session.flush()
    assert (await client.post(URL, json=body(eventId="producer-4"), headers=AUTH)).json()["deduped"] is True
    # Exact eventId replay remains original even when a definition is later unavailable.
    et.status = TypeStatus.draft
    await session.flush()
    assert (await client.post(URL, json=body(), headers=AUTH)).json()["deduped"] is False


async def test_event_ingress_rejects_invalid_types_payload_and_text_without_writes(ingress, client):
    session, et = ingress
    for changes, expected in (
        ({"eventType": "unknown"}, 404),
        ({"objectType": "Store"}, 422),
        ({"payload": {}}, 422),
        ({"payload": {"amount": True}}, 422),
        ({"occurredAt": "2026-10-25 10:00"}, 422),
        ({"extra": "no"}, 422),
        ({"source": "\x00"}, 422),
        ({"payload": {"amount": 1, "x": "\ud800"}}, 422),
        ({"payload": {"amount": 1, "x": list(range(513))}}, 422),
    ):
        response = await client.post(
            URL, content=json.dumps(body(**changes)), headers={**AUTH, "Content-Type": "application/json"}
        )
        assert response.status_code == expected, response.text
    deep = {}
    for _ in range(18):
        deep = {"x": deep}
    assert (await client.post(URL, json=body(payload=deep), headers=AUTH)).status_code == 422
    assert (await client.post(URL, content=b"{" + b" " * 65536, headers=AUTH)).status_code == 413
    et.dedup_key_rule = "订单号 + 状态"
    await session.flush()
    response = await client.post(URL, json=body(dedupKey=None), headers=AUTH)
    assert response.status_code == 422 and response.json()["code"] == "EVENT_DEDUP_KEY_REQUIRED"
    et.dedup_window = "未知窗口"
    await session.flush()
    assert (await client.post(URL, json=body(), headers=AUTH)).status_code == 409
    et.status = TypeStatus.draft
    await session.flush()
    assert (await client.post(URL, json=body(), headers=AUTH)).status_code == 409
    assert await session.scalar(select(func.count()).select_from(Event)) == 0
    assert await session.scalar(select(func.count()).select_from(EventDedup)) == 0


async def _wait_lock(observer, pid, task, locktype):
    async with asyncio.timeout(5):
        while not await observer.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND locktype=:kind AND NOT granted)"),
            {"pid": pid, "kind": locktype},
        ):
            if task.done():
                await task
                pytest.fail("expected a real database lock wait")


@pytest.mark.parametrize("same_id", [True, False])
async def test_event_ingress_real_concurrent_replay_and_dedup(engine, same_id):
    token = uuid4().hex[:16]
    type_code = f"ingress_{token}"
    async with AsyncSession(engine) as setup:
        setup.add(event_type(type_code))
        await setup.commit()
    try:
        async with (
            engine.connect() as c1,
            engine.connect() as c2,
            AsyncSession(bind=c1, expire_on_commit=False) as a,
            AsyncSession(bind=c2, expire_on_commit=False) as b,
        ):
            pid = await b.scalar(text("SELECT pg_backend_pid()"))
            await b.rollback()
            first_body = IngressIn.model_validate(body(eventId=f"{token}-1", eventType=type_code))
            second_body = (
                first_body
                if same_id
                else IngressIn.model_validate(body(eventId=f"{token}-2", eventType=type_code))
            )
            first = await receive(a, first_body)
            task = asyncio.create_task(receive(b, second_body))
            try:
                await _wait_lock(a, pid, task, "advisory")
                await a.commit()
                second = await asyncio.wait_for(task, 5)
                assert first["deduped"] is False
                assert second["deduped"] is (not same_id)
                await b.commit()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await a.rollback()
                await b.rollback()
        async with AsyncSession(engine) as check:
            et_id = await check.scalar(select(EventType.id).where(EventType.code == type_code))
            assert await check.scalar(
                select(func.count()).select_from(Event).where(Event.event_type_id == et_id)
            ) == (1 if same_id else 2)
            assert (
                await check.scalar(
                    select(func.count()).select_from(EventDedup).where(EventDedup.event_type_id == et_id)
                )
                == 1
            )
    finally:
        async with AsyncSession(engine) as cleanup:
            et_id = await cleanup.scalar(select(EventType.id).where(EventType.code == type_code))
            await cleanup.execute(delete(Event).where(Event.event_type_id == et_id))
            await cleanup.execute(delete(EventType).where(EventType.id == et_id))
            await cleanup.commit()


async def test_event_ingress_type_share_lock_and_atomic_rollback(engine):
    token = uuid4().hex[:16]
    code = f"ingress_{token}"
    async with AsyncSession(engine) as setup:
        setup.add(event_type(code))
        await setup.commit()
    try:
        async with (
            engine.connect() as c1,
            engine.connect() as c2,
            AsyncSession(bind=c1, expire_on_commit=False) as publisher,
            AsyncSession(bind=c2, expire_on_commit=False) as receiver,
        ):
            pid = await receiver.scalar(text("SELECT pg_backend_pid()"))
            await receiver.rollback()
            et = await publisher.scalar(select(EventType).where(EventType.code == code))
            type_id = et.id
            et.version = "v2"
            publisher.add(
                EventPayloadField(
                    event_type_id=et.id,
                    seq=1,
                    api="new_required",
                    name="新必填",
                    type="字符串",
                    required=True,
                )
            )
            await publisher.flush()
            request = IngressIn.model_validate(body(eventId=f"{token}-1", eventType=code))
            task = asyncio.create_task(receive(receiver, request))
            try:
                await _wait_lock(publisher, pid, task, "transactionid")
                assert not task.done(), "ingress must not read metadata while publication owns its type row"
                await publisher.rollback()
                assert (await asyncio.wait_for(task, 5))["deduped"] is False
                # Rolling back receipt must undo both the event and its business-dedup state.
                await receiver.rollback()
                assert (
                    await receiver.scalar(
                        select(func.count()).select_from(Event).where(Event.code == request.event_id)
                    )
                    == 0
                )
                assert (
                    await receiver.scalar(
                        select(func.count())
                        .select_from(EventDedup)
                        .where(EventDedup.event_type_id == type_id)
                    )
                    == 0
                )
                assert (await receive(receiver, request))["deduped"] is False
                await receiver.commit()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await receiver.rollback()
                await publisher.rollback()
    finally:
        async with AsyncSession(engine) as cleanup:
            et_id = await cleanup.scalar(select(EventType.id).where(EventType.code == code))
            await cleanup.execute(delete(Event).where(Event.event_type_id == et_id))
            await cleanup.execute(delete(EventType).where(EventType.id == et_id))
            await cleanup.commit()
