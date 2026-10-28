"""Real PostgreSQL transactions -> production delivery code -> independent loopback receiver.

No worker process or mocked HTTP/signing functions. Every case owns a fresh test workspace;
the sandbox channel avoids production object mutations. Run from backend with RENGU_TEST_DB set.
"""

import hashlib
import hmac
import json
import os
import socket
import threading
from collections import deque
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import clock
from app.core.config import get_settings
from app.modules.action.models import TypeStatus
from app.modules.event import delivery
from app.modules.event.models import (
    Event,
    EventDelivery,
    EventDeliveryRecord,
    EventOutcome,
    EventSource,
    EventType,
)
from app.modules.ontology.models import OntologyWorkspace

SECRET = "e2e-webhook-secret-0123456789"


@pytest.fixture
def receiver():
    state = SimpleNamespace(secret=SECRET, now=int(clock.now().timestamp()), script=deque(), requests=[])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            timestamp = self.headers.get("X-Signature-Timestamp", "")
            signature = self.headers.get("X-Signature-256", "")
            expected = (
                "sha256="
                + hmac.new(state.secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
            )
            try:
                fresh = abs(state.now - int(timestamp)) <= 300
            except ValueError:
                fresh = False
            verified = fresh and hmac.compare_digest(expected, signature)
            state.requests.append({"body": raw, "headers": dict(self.headers), "verified": verified})
            if not verified:
                status, headers = 401, {}
            else:
                step = state.script.popleft() if state.script else (204, {})
                if step == "close":
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                status, headers = step
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        state.url = f"http://127.0.0.1:{server.server_port}/webhook"
        thread.start()
        try:
            yield state
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.fixture
async def channel(engine, monkeypatch, receiver):
    database = os.environ.get("RENGU_TEST_DB", "rengu_test")
    assert database == "rengu_test" or database.startswith("rengu_test_")
    assert engine.url.database == database
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        workspace = OntologyWorkspace(code="webhook-e2e-" + uuid4().hex, name="Webhook E2E")
        session.add(workspace)
        await session.flush()
        ontology_id = workspace.id
    sessions = async_sessionmaker(engine, expire_on_commit=False, info={"ontology_id": ontology_id})
    subscriber = {"name": "receiver", "kind": "外部订阅", "filter": "全部", "status": "启用"}
    async with sessions() as session, session.begin():
        event_type = EventType(
            code="e2e_event",
            api="e2e_event",
            name="Webhook E2E",
            source=EventSource.external,
            status=TypeStatus.published,
            version="v1",
            retention_days=1,
        )
        session.add(event_type)
        await session.flush()
        event = Event(
            code="webhook-e2e",
            event_type_id=event_type.id,
            occurred_at=clock.now(),
            object_id="order-1",
            obj_kind="order",
            producer_object_type="SalesOrder",
            is_test=True,
            outcome=EventOutcome.pending,
            payload={"amount": 12.5, "note": "中文签名"},
            trace_id="e2e-trace",
            definition_snapshot={
                "id": "e2e_event",
                "version": "v1",
                "objKind": "order",
                "payload": [],
                "subscribers": [subscriber],
            },
        )
        session.add(event)
        await session.flush()
        await delivery.discover(session)
        row = await session.scalar(select(EventDelivery).where(EventDelivery.event_id == event.id))
        row_id = row.id
    route = {
        "url": "https://production.invalid/webhook",
        "test_url": receiver.url,
        "idempotent": True,
        "test_signing_secret": SECRET,
        "max_retries": 2,
    }
    route_key = f"{ontology_id}/e2e_event/receiver"
    settings = get_settings()
    monkeypatch.setattr(settings, "event_delivery_http_enabled", True)

    def configure(**changes):
        route.update(changes)
        monkeypatch.setattr(settings, "event_delivery_routes", SecretStr(json.dumps({route_key: route})))

    configure()
    return SimpleNamespace(
        sessions=sessions, row_id=row_id, ontology_id=ontology_id, configure=configure, receiver=receiver
    )


async def tick(channel, at):
    """Same due/claim/execute boundaries as the worker, with a fresh session and committed claim."""
    channel.receiver.now = int(at.timestamp())
    with clock.frozen(at):
        async with channel.sessions() as session, session.begin():
            ids = await delivery.due(session)
        for row_id in ids:
            assert row_id == channel.row_id
            async with channel.sessions() as session, session.begin():
                claimed = await delivery.prepare(session, row_id)
            if claimed:
                async with channel.sessions() as session, session.begin():
                    await delivery.execute(session, row_id)


async def retained(channel):
    async with channel.sessions() as session:
        row = await session.get(EventDelivery, channel.row_id)
        history = list(
            await session.scalars(
                select(EventDeliveryRecord)
                .where(EventDeliveryRecord.delivery_id == row.id)
                .order_by(EventDeliveryRecord.id)
            )
        )
        event = await session.get(Event, row.event_id)
        return row, history, event


@pytest.mark.parametrize("first", [(503, {}), (429, {"Retry-After": "120"}), "close"])
async def test_signed_delivery_retries_only_when_due(channel, receiver, first):
    receiver.script.extend([first, (204, {})])
    start = clock.now()
    await tick(channel, start)
    row, history, _ = await retained(channel)
    delay = 120 if first != "close" and first[0] == 429 else 30
    assert (row.state, row.error, row.attempts) == ("pending", "HTTP_RETRY_SCHEDULED", 1)
    assert row.next_at == start + timedelta(seconds=delay) and row.finished_at is None
    assert [(r.kind, r.attempt) for r in history] == [("attempt_started", 1), ("attempt_finished", 1)]
    await tick(channel, row.next_at - timedelta(seconds=1))
    assert len(receiver.requests) == 1
    await tick(channel, row.next_at)
    row, history, event = await retained(channel)
    assert (row.state, row.error, row.attempts, row.next_at) == ("delivered", None, 2, None)
    assert event.outcome == EventOutcome.delivered and event.subscriber_count == 1
    assert [(r.kind, r.attempt, r.state) for r in history] == [
        ("attempt_started", 1, "processing"),
        ("attempt_finished", 1, "pending"),
        ("attempt_started", 2, "processing"),
        ("attempt_finished", 2, "delivered"),
    ]
    assert [r.detail["autoAttempt"] for r in history if r.kind == "attempt_started"] == [1, 2]
    first_request, second_request = receiver.requests
    assert all(r["verified"] for r in receiver.requests)
    assert first_request["body"] == second_request["body"]
    for request in receiver.requests:
        headers = request["headers"]
        assert headers["Idempotency-Key"] == delivery.delivery_key(event, "receiver")
        assert headers["X-Ontology-Id"] == str(channel.ontology_id)
        assert headers["Content-Type"] == "application/json"
        envelope = json.loads(request["body"])
        assert envelope["payload"] == {"amount": 12.5, "note": "中文签名"}
        assert envelope["ontologyId"] == channel.ontology_id and envelope["isTest"] is True
        assert envelope["eventId"] == event.code and envelope["traceId"] == "e2e-trace"
    assert (
        first_request["headers"]["X-Signature-Timestamp"]
        != second_request["headers"]["X-Signature-Timestamp"]
    )
    assert first_request["headers"]["X-Signature-256"] != second_request["headers"]["X-Signature-256"]
    await tick(channel, start + timedelta(hours=1))
    assert len(receiver.requests) == 2


@pytest.mark.parametrize("failure", [(503, {}), (429, {}), "close"])
async def test_retry_budget_and_exponential_backoff_survive_new_sessions(channel, receiver, failure):
    receiver.script.extend([failure] * 4)
    start = clock.now()
    await tick(channel, start)
    row, _, _ = await retained(channel)
    assert row.next_at == start + timedelta(seconds=30)
    await tick(channel, row.next_at)
    row, _, _ = await retained(channel)
    assert row.next_at == start + timedelta(seconds=90)
    await tick(channel, row.next_at)
    row, history, event = await retained(channel)
    expected = (
        ("unknown", "HTTP_RESULT_UNKNOWN_REQUIRES_REVIEW")
        if failure == "close"
        else ("failed", "HTTP_SUBSCRIBER_REJECTED")
    )
    assert (row.state, row.error) == expected
    assert row.attempts == 3 and row.next_at is None and row.finished_at is not None
    assert len(history) == 6 and event.outcome == EventOutcome.failed
    assert all(r["verified"] for r in receiver.requests)
    assert len({r["headers"]["Idempotency-Key"] for r in receiver.requests}) == 1
    await tick(channel, start + timedelta(hours=1))
    assert len(receiver.requests) == 3


@pytest.mark.parametrize("status", [302, 400, 401, 403, 409])
async def test_permanent_rejection_never_retries_or_follows_redirect(channel, receiver, status):
    receiver.script.extend([(status, {"Location": receiver.url + "/redirected"}), (204, {})])
    await tick(channel, clock.now())
    await tick(channel, clock.now() + timedelta(hours=1))
    row, history, _ = await retained(channel)
    assert (row.state, row.error, row.attempts) == ("failed", "HTTP_SUBSCRIBER_REJECTED", 1)
    assert row.result == {"httpStatus": status} and row.next_at is None
    assert len(history) == 2 and len(receiver.requests) == 1 and receiver.requests[0]["verified"]


async def test_wrong_receiver_key_rejects_actual_application_signature(channel, receiver):
    receiver.secret = "different-receiver-secret-0123456789"
    await tick(channel, clock.now())
    row, _, _ = await retained(channel)
    assert row.state == "failed" and row.result == {"httpStatus": 401}
    assert len(receiver.requests) == 1 and not receiver.requests[0]["verified"]


@pytest.mark.parametrize("secret", ["short", "fernet$broken"])
async def test_invalid_configured_secret_never_sends_unsigned(channel, receiver, secret):
    channel.configure(test_signing_secret=secret)
    await tick(channel, clock.now())
    row, history, _ = await retained(channel)
    assert row.state == "pending" and row.attempts == 0
    assert row.error in {"SIGNING_SECRET_INVALID", "SIGNING_SECRET_UNDECRYPTABLE"}
    assert receiver.requests == [] and all(r.kind == "preflight" for r in history)


@pytest.mark.parametrize("mutation", ["body", "timestamp", "signature", "missing"])
async def test_receiver_rejects_tampering_and_expired_signatures(receiver, mutation):
    raw = b'{"probe":"receiver-self-check"}'
    timestamp = str(receiver.now - (301 if mutation == "timestamp" else 0))
    signature = (
        "sha256=" + hmac.new(SECRET.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
    )
    headers = {"X-Signature-Timestamp": timestamp, "X-Signature-256": signature}
    if mutation == "body":
        raw += b" "
    elif mutation == "signature":
        headers["X-Signature-256"] = "sha256=" + "0" * 64
    elif mutation == "missing":
        headers = {}
    async with httpx.AsyncClient(trust_env=False) as client:
        response = await client.post(receiver.url, content=raw, headers=headers)
    assert response.status_code == 401 and not receiver.requests[0]["verified"]
