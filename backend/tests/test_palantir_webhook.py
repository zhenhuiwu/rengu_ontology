"""Tier-1 Webhook 出站：HMAC-SHA256 签名、可重试失败的指数退避、幂等键稳定、逐次尝试留痕。

只用本机 127.0.0.1 上的 mock HTTP 服务；不连外网、不启 worker。动作副作用的授权/映射复核
（recheck_effect）已有覆盖，这里替换为固定结果，只验证出站发送与重试编排。
"""

import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.common.outbox import emit
from app.core import clock
from app.modules.data.secrets import encrypt_password
from app.modules.event import delivery as event_delivery
from app.modules.event.models import Event, EventDelivery, EventDeliveryRecord
from app.modules.integration import action_effects, signing
from tests.test_action_event import ae  # noqa: F401  (ae 是 fixture)

SECRET = "tier1-webhook-secret-0123456789"


class MockReceiver:
    """按脚本依次返回 (status, body, delay_s)；"close" 表示不回包直接断开（网络错误）。"""

    def __init__(self):
        self.script, self.requests = [], []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                receiver.requests.append({"headers": dict(self.headers), "body": body})
                step = receiver.script.pop(0) if receiver.script else (200, {}, 0)
                if step == "close":
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                status, payload, delay = step
                time.sleep(delay)
                data = json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    server = MockReceiver()
    yield server
    server.close()


def test_signature_roundtrip_and_secret_resolution():
    body = signing.encode_body({"a": "中文", "b": 1})
    secret = signing.resolve_secret(SecretStr(SECRET))
    headers = signing.signed_headers({"Idempotency-Key": "k"}, body, secret)
    ts = headers[signing.TIMESTAMP_HEADER]
    assert headers[signing.SIGNATURE_HEADER] == signing.signature(secret, ts, body)
    assert headers[signing.SIGNATURE_HEADER].startswith("sha256=")
    assert len(headers[signing.SIGNATURE_HEADER]) == len("sha256=") + 64
    assert signing.verify(secret, ts, body, headers[signing.SIGNATURE_HEADER])
    assert not signing.verify(secret, ts, body + b" ", headers[signing.SIGNATURE_HEADER])
    assert not signing.verify(b"x" * 32, ts, body, headers[signing.SIGNATURE_HEADER])
    stale = str(int(ts) - 301)
    assert not signing.verify(secret, stale, body, signing.signature(secret, stale, body))
    # 无密钥：与原行为一致，不出现签名头。
    assert signing.signed_headers({"Idempotency-Key": "k"}, body, None) == {
        "Idempotency-Key": "k", "Content-Type": "application/json"}
    assert signing.resolve_secret(None) is None
    assert signing.resolve_secret(encrypt_password(SECRET)) == SECRET.encode()
    for bad in ("short", "fernet$not-a-token", "x" * 20 + "\n"):
        with pytest.raises(signing.SigningSecretInvalid):
            signing.resolve_secret(bad)


def test_retry_classification_and_backoff():
    assert [signing.retryable_status(s) for s in (200, 400, 404, 409, 429, 500, 503)] == [
        False, False, False, False, True, True, True]
    assert [signing.backoff(n).total_seconds() for n in (1, 2, 3, 10)] == [30, 60, 120, 900]
    assert signing.backoff(1, "120") == timedelta(seconds=120)
    assert signing.backoff(1, "Wed, 21 Oct") == timedelta(seconds=30)
    assert [signing.retry_budget(v) for v in (None, -1, 2, 9, "x")] == [0, 0, 2, 5, 0]


# ---------- action effects（outbox 行 + available_at 退避） ----------


def scope_of(session):
    class Proxy:
        def __getattr__(self, name):
            return getattr(session, name)

        def begin(self):
            return session.begin_nested()

    @asynccontextmanager
    async def scope():
        yield Proxy()

    return lambda ontology_id: scope


async def effect_row(session, url):
    row = await emit(session, action_effects.TOPIC, {
        "runId": "RUN-T1", "actor": 1, "typeVersion": 1,
        "effect": {"kind": "webhook", "target": "sys"},
        "data": {"url": url, "payload": {"orderId": "O-1", "note": "签名"}}, "state": "queued"},
        event_id="effect:tier1-" + url.rsplit(":", 1)[1].replace("/", ""))
    return row


def binding(url, *, secret=SECRET, max_retry=2, timeout_ms=1000):
    config = SimpleNamespace(token=SecretStr("bearer-x"),
                             signing_secret=SecretStr(secret) if secret else None)
    system = SimpleNamespace(max_retry=max_retry, timeout_ms=timeout_ms)
    run, adapter = SimpleNamespace(code="RUN-T1"), SimpleNamespace(config=config)
    return AsyncMock(return_value=(run, system, adapter, url))


SUCCESS = {"status": "SUCCESS", "receiptId": "R-1"}


async def drive_effects(session, rounds):
    """每轮推进时钟越过退避窗口后再扫描一次（模拟 worker 每分钟 cron）。"""
    start = clock.now()
    for i in range(rounds):
        with clock.frozen(start + timedelta(minutes=20 * i)):
            await action_effects.dispatch_effects(1)


async def test_effect_signed_retry_on_503_then_success(session, receiver):
    receiver.script = [(503, {}, 0), (200, SUCCESS, 0)]
    row = await effect_row(session, receiver.url)
    with patch.object(action_effects, "scoped_sessions", scope_of(session)), \
            patch.object(action_effects, "recheck_effect", binding(receiver.url)):
        await action_effects.dispatch_effect(row.id, 1)
        await session.refresh(row)
        assert row.last_error == action_effects.RETRY_SCHEDULED and row.payload["state"] == "retrying"
        assert row.available_at == clock.now() + timedelta(seconds=30) and row.attempts == 1
        await action_effects.dispatch_effect(row.id, 1)  # 退避未到期：不发
        assert len(receiver.requests) == 1
        await drive_effects(session, 2)
    await session.refresh(row)
    assert len(receiver.requests) == 2
    assert row.dispatched_at is not None and row.last_error is None and row.payload["state"] == "succeeded"
    assert row.payload["receiptId"] == "R-1" and row.attempts == 2
    assert [(a["attempt"], a["httpStatus"], a["error"], a["retryable"], a["signed"])
            for a in row.payload["attempts"]] == [
        (1, 503, "EXTERNAL_HTTP_503", True, True), (2, 200, None, False, True)]
    keys = {r["headers"]["Idempotency-Key"] for r in receiver.requests}
    assert keys == {row.event_id}
    secret = SECRET.encode()
    for req in receiver.requests:
        h = req["headers"]
        assert h["Content-Type"] == "application/json" and h["Authorization"] == "Bearer bearer-x"
        assert h["X-Signature-256"] == signing.signature(secret, h["X-Signature-Timestamp"], req["body"])
        assert json.loads(req["body"])["payload"] == {"orderId": "O-1", "note": "签名"}


async def test_effect_timeout_retried_then_exhausted_is_unknown(session, receiver):
    receiver.script = [(200, SUCCESS, 1.0), (200, SUCCESS, 1.0)]
    row = await effect_row(session, receiver.url)
    with patch.object(action_effects, "scoped_sessions", scope_of(session)), \
            patch.object(action_effects, "recheck_effect",
                         binding(receiver.url, max_retry=1, timeout_ms=200)):
        await drive_effects(session, 4)
    await session.refresh(row)
    assert len(receiver.requests) == 2  # 1 次首发 + max_retry=1 次重试，之后不再发
    assert row.payload["state"] == "unknown" and row.last_error == "EXTERNAL_OUTCOME_UNKNOWN"
    assert row.dispatched_at is None
    assert [a["retryable"] for a in row.payload["attempts"]] == [True, True]
    assert len({r["headers"]["Idempotency-Key"] for r in receiver.requests}) == 1


async def test_effect_400_fails_without_retry(session, receiver):
    receiver.script = [(400, {"code": "BAD"}, 0)]
    row = await effect_row(session, receiver.url)
    with patch.object(action_effects, "scoped_sessions", scope_of(session)), \
            patch.object(action_effects, "recheck_effect", binding(receiver.url)):
        await drive_effects(session, 3)
    await session.refresh(row)
    assert len(receiver.requests) == 1
    assert row.payload["state"] == "failed" and row.last_error == "EXTERNAL_HTTP_400" and row.attempts == 1
    assert row.payload["attempts"][0]["retryable"] is False


async def test_effect_without_secret_is_unsigned(session, receiver):
    receiver.script = [(200, SUCCESS, 0)]
    row = await effect_row(session, receiver.url)
    with patch.object(action_effects, "scoped_sessions", scope_of(session)), \
            patch.object(action_effects, "recheck_effect", binding(receiver.url, secret=None)):
        await action_effects.dispatch_effect(row.id, 1)
    await session.refresh(row)
    headers = receiver.requests[0]["headers"]
    assert "X-Signature-256" not in headers and "X-Signature-Timestamp" not in headers
    assert headers["Idempotency-Key"] == row.event_id and row.payload["state"] == "succeeded"
    assert row.payload["attempts"][0]["signed"] is False


async def test_effect_invalid_secret_never_sends_unsigned(session, receiver):
    row = await effect_row(session, receiver.url)
    with patch.object(action_effects, "scoped_sessions", scope_of(session)), \
            patch.object(action_effects, "recheck_effect", binding(receiver.url, secret="fernet$broken")):
        await action_effects.dispatch_effect(row.id, 1)
    await session.refresh(row)
    assert receiver.requests == []
    assert row.payload["state"] == "rejected" and row.last_error == "SIGNING_SECRET_UNDECRYPTABLE"


# ---------- event delivery（pending + next_at 退避，沿用 attempt_started/attempt_finished 记录） ----------


def route_settings(url, **route):
    config = {"tier1_evt/hook": {"url": "https://prod.invalid/hook", "test_url": url, "idempotent": True,
                                 "test_signing_secret": SECRET, **route}}
    return SimpleNamespace(event_delivery_http_enabled=True, env="dev",
                           event_delivery_routes=SecretStr(json.dumps(config)))


async def delivery_row(session):
    event = await session.scalar(select(Event).order_by(Event.id).limit(1))
    event.is_test, event.payload = True, {"orderId": "O-1"}
    event.definition_snapshot = {"id": "tier1_evt", "version": "1", "objKind": "order", "payload": [],
                                 "subscribers": []}
    row = EventDelivery(event_id=event.id, subscriber_name="hook", subscription={"filter": "全部"},
                        state="pending", attempts=0, next_at=clock.now())
    session.add(row)
    await session.flush()
    return event, row


async def sweep(session, row, rounds):
    start = clock.now()
    for i in range(rounds):
        with clock.frozen(start + timedelta(minutes=20 * i)):
            await session.refresh(row)
            if row.state == "pending" and row.next_at <= clock.now() and await event_delivery.prepare(
                    session, row.id):
                await event_delivery.execute(session, row.id)


async def records(session, row):
    return list(await session.scalars(select(EventDeliveryRecord).where(
        EventDeliveryRecord.delivery_id == row.id).order_by(EventDeliveryRecord.id)))


async def test_event_delivery_signed_retry_then_delivered(ae, receiver):  # noqa: F811
    receiver.script = ["close", (503, {}, 0), (200, {}, 0)]
    event, row = await delivery_row(ae)
    with patch.object(event_delivery, "get_settings", return_value=route_settings(receiver.url)):
        await sweep(ae, row, 1)
        await ae.refresh(row)
        assert row.state == "pending" and row.error == event_delivery.RETRY_SCHEDULED
        assert row.next_at == clock.now() + timedelta(seconds=30)
        await sweep(ae, row, 4)
    await ae.refresh(row)
    assert row.state == "delivered" and row.attempts == 3 and len(receiver.requests) == 3
    assert {r["headers"]["Idempotency-Key"] for r in receiver.requests} == {
        event_delivery.delivery_key(event, "hook")}
    for req in receiver.requests:
        h = req["headers"]
        expected = signing.signature(SECRET.encode(), h["X-Signature-Timestamp"], req["body"])
        assert h["X-Signature-256"] == expected
        assert json.loads(req["body"])["eventId"] == event.code
    history = [(r.kind, r.attempt, r.state, r.error, (r.detail or {}).get("autoAttempt"))
               for r in await records(ae, row)]
    assert history == [
        ("attempt_started", 1, "processing", None, 1),
        ("attempt_finished", 1, "pending", "HTTP_RETRY_SCHEDULED", None),
        ("attempt_started", 2, "processing", None, 2),
        ("attempt_finished", 2, "pending", "HTTP_RETRY_SCHEDULED", None),
        ("attempt_started", 3, "processing", None, 3),
        ("attempt_finished", 3, "delivered", None, None),
    ]


async def test_event_delivery_400_fails_immediately(ae, receiver):  # noqa: F811
    receiver.script = [(400, {}, 0)]
    _, row = await delivery_row(ae)
    with patch.object(event_delivery, "get_settings", return_value=route_settings(receiver.url)):
        await sweep(ae, row, 3)
    await ae.refresh(row)
    assert row.state == "failed" and row.error == "HTTP_SUBSCRIBER_REJECTED" and row.attempts == 1
    assert len(receiver.requests) == 1


async def test_event_delivery_retry_budget_exhausted(ae, receiver):  # noqa: F811
    receiver.script = [(503, {}, 0), (429, {}, 0), (503, {}, 0)]
    _, row = await delivery_row(ae)
    settings = route_settings(receiver.url, max_retries=1)
    with patch.object(event_delivery, "get_settings", return_value=settings):
        await sweep(ae, row, 5)
    await ae.refresh(row)
    assert len(receiver.requests) == 2 and row.attempts == 2
    assert row.state == "failed" and row.error == "HTTP_SUBSCRIBER_REJECTED"
    assert row.result == {"httpStatus": 429}


async def test_event_delivery_without_secret_unchanged_and_rotation_keeps_binding(ae, receiver):  # noqa: F811
    receiver.script = [(200, {}, 0)]
    _, row = await delivery_row(ae)
    plain = route_settings(receiver.url, test_signing_secret=None)
    with patch.object(event_delivery, "get_settings", return_value=plain):
        assert await event_delivery.prepare(ae, row.id)
    # 密钥轮换不改变路由指纹，已开始的尝试仍可执行。
    assert event_delivery.route_fingerprint({"url": "u", "signing_secret": "a"}) == \
        event_delivery.route_fingerprint({"url": "u", "signing_secret": "b", "test_signing_secret": "c"})
    with patch.object(event_delivery, "get_settings", return_value=plain):
        await event_delivery.execute(ae, row.id)
    await ae.refresh(row)
    headers = receiver.requests[0]["headers"]
    assert row.state == "delivered" and "X-Signature-256" not in headers


async def test_event_delivery_invalid_secret_stays_pending_without_send(ae, receiver):  # noqa: F811
    _, row = await delivery_row(ae)
    with patch.object(event_delivery, "get_settings",
                      return_value=route_settings(receiver.url, test_signing_secret="short")):
        assert not await event_delivery.prepare(ae, row.id)
    await ae.refresh(row)
    assert row.state == "pending" and row.error == "SIGNING_SECRET_INVALID" and row.attempts == 0
    assert receiver.requests == []
