"""Outbound HTTPS uses an explicit private CA and still rejects untrusted certificates/IP SANs."""

import ipaddress
import json
import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import SecretStr
from sqlalchemy import select

from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.action.models import TypeStatus
from app.modules.event import delivery
from app.modules.event.models import Event, EventDelivery, EventOutcome, EventSource, EventType
from app.modules.integration import action_effects, execution, signing
from app.modules.integration.adapters import AdapterConfig, HttpAdapter, tls_verify
from app.modules.integration.models import ExtSystem, ExtSystemStatus, WritebackCall
from tests.test_palantir_webhook import SECRET, binding, effect_row, scope_of
from tests.test_use_ops_runtime import API, payload, runtime  # noqa: F401
from tests.use_ops_support import _no_external_http  # noqa: F401


@pytest.fixture
def https_receiver(tmp_path, request):
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test-only local CA")])
    root = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(root_key, hashes.SHA256())
    )
    host = "127.0.0.2" if getattr(request, "param", None) == "wrong-san" else "127.0.0.1"
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Loopback receiver")]))
        .issuer_name(root.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .sign(root_key, hashes.SHA256())
    )
    ca_file, cert_file, key_file = (tmp_path / f"{part}.pem" for part in ("ca", "server", "key"))
    ca_file.write_bytes(root.public_bytes(serialization.Encoding.PEM))
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    state = SimpleNamespace(ca_file=ca_file, host=host, requests=[])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            assert signing.verify(
                SECRET.encode(),
                self.headers[signing.TIMESTAMP_HEADER],
                body,
                self.headers[signing.SIGNATURE_HEADER],
            )
            self.respond(body)

        def respond(self, body=None):
            state.requests.append(body)
            data = json.dumps({"status": "SUCCESS", "receiptId": "TLS-RECEIPT"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        state.url = f"https://127.0.0.1:{server.server_port}/hook"
        request.node.local_webhook_endpoint = state.url
        request.node.local_webhook_methods = ("GET", "POST")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield state
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize(
    "https_receiver,trusted", [(None, True), (None, False), ("wrong-san", True)], indirect=["https_receiver"]
)
async def test_adapter_https_requires_trusted_ca_and_matching_ip(https_receiver, trusted, monkeypatch):
    monkeypatch.setattr(get_settings(), "integration_ca_file", https_receiver.ca_file if trusted else None)
    # Environment proxy settings cannot redirect a request even when an explicit CA is used.
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    system = SimpleNamespace(endpoint=https_receiver.url, ontology_id=1, timeout_ms=1000)
    adapter = HttpAdapter(system, AdapterConfig(probe_path="/hook"))
    if trusted and https_receiver.host == "127.0.0.1":
        assert (await adapter.request("probe")).status_code == 200
        assert adapter.verify.verify_mode == ssl.CERT_REQUIRED and adapter.verify.check_hostname
    else:
        with pytest.raises(AppError) as error:
            await adapter.request("probe")
        assert error.value.code == "EXT_QUERY_FAILED" and https_receiver.requests == []


async def test_effect_https_and_invalid_ca_before_claim(session, https_receiver, monkeypatch, tmp_path):
    settings = get_settings()
    row = await effect_row(session, https_receiver.url)
    with (
        patch.object(action_effects, "scoped_sessions", scope_of(session)),
        patch.object(action_effects, "recheck_effect", binding(https_receiver.url)),
    ):
        monkeypatch.setattr(settings, "integration_ca_file", tmp_path / "missing-ca.pem")
        await action_effects.dispatch_effect(row.id, 1)
        await session.refresh(row)
        assert row.payload["state"] == "rejected" and row.last_error == "INTEGRATION_TLS_CONFIG_INVALID"
        assert not https_receiver.requests
        row.payload, row.attempts, row.last_error = {**row.payload, "state": "queued"}, 0, None
        await session.flush()
        monkeypatch.setattr(settings, "integration_ca_file", https_receiver.ca_file)
        await action_effects.dispatch_effect(row.id, 1)
    await session.refresh(row)
    assert row.payload["state"] == "succeeded" and row.payload["receiptId"] == "TLS-RECEIPT"


async def test_event_https_and_invalid_ca_before_claim(session, https_receiver, monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "event_delivery_http_enabled", True)
    monkeypatch.setattr(
        settings,
        "event_delivery_routes",
        SecretStr(
            json.dumps(
                {
                    "tls/hook": {
                        "url": "https://production.invalid/hook",
                        "test_url": https_receiver.url,
                        "idempotent": True,
                        "test_signing_secret": SECRET,
                    }
                }
            )
        ),
    )
    kind = EventType(
        code="tls",
        name="TLS",
        api="TlsEvent",
        source=EventSource.external,
        status=TypeStatus.published,
        version="v1",
        retention_days=1,
    )
    session.add(kind)
    await session.flush()
    event = Event(
        code="TLS-EVENT",
        event_type_id=kind.id,
        occurred_at=clock.now(),
        object_id="test",
        outcome=EventOutcome.pending,
        is_test=True,
        payload={},
        definition_snapshot={
            "id": "tls",
            "version": "v1",
            "objKind": "test",
            "payload": [],
            "subscribers": [],
        },
    )
    session.add(event)
    await session.flush()
    row = EventDelivery(
        event_id=event.id,
        subscriber_name="hook",
        subscription={"filter": "全部"},
        state="pending",
        attempts=0,
    )
    session.add(row)
    await session.flush()
    monkeypatch.setattr(settings, "integration_ca_file", tmp_path / "missing-ca.pem")
    assert not await delivery.prepare(session, row.id)
    assert row.state == "pending" and row.error == "INTEGRATION_TLS_CONFIG_INVALID" and row.attempts == 0
    monkeypatch.setattr(settings, "integration_ca_file", https_receiver.ca_file)
    assert await delivery.prepare(session, row.id)
    await delivery.execute(session, row.id)
    assert row.state == "delivered" and row.attempts == 1 and len(https_receiver.requests) == 1


async def test_action_https_and_invalid_ca_before_claim(
    runtime,  # noqa: F811
    client,
    https_receiver,
    monkeypatch,
    tmp_path,
):
    session, action, _ = runtime
    settings = get_settings()
    system = ExtSystem(
        code="TLS-EXT",
        name="local TLS",
        endpoint=https_receiver.url,
        timeout_ms=1000,
        idempotent=True,
        idem_header="Idempotency-Key",
        status=ExtSystemStatus.ok,
        env="test",
    )
    session.add(system)
    await session.flush()
    action.sys_id = system.id
    await session.flush()
    monkeypatch.setattr(settings, "integration_real_connect", True)
    monkeypatch.setattr(settings, "integration_ca_file", https_receiver.ca_file)
    monkeypatch.setattr(
        settings,
        "integration_adapters",
        SecretStr(
            json.dumps(
                {"1/TLS-EXT": {"submit_path": "/hook", "actions": [action.code], "signing_secret": SECRET}}
            )
        ),
    )
    staged = await client.post(f"{API}/action-runs:stage", json=payload())
    assert staged.status_code == 201, staged.text
    code = staged.json()["id"]
    confirmed = await client.post(f"{API}/action-runs/{code}:confirm")
    assert confirmed.status_code == 200 and confirmed.json()["status"] == "结果未知", confirmed.text
    call = await session.scalar(select(WritebackCall).where(WritebackCall.msg == execution.QUEUED))
    monkeypatch.setattr(settings, "integration_ca_file", https_receiver.ca_file)
    with patch.object(execution, "scoped_sessions", scope_of(session)):
        await execution.dispatch_one(call.id, 1)
    await session.refresh(call)
    assert call.http_status == 200 and call.attempt == 1 and len(https_receiver.requests) == 1
    final = await client.get(f"{API}/action-runs/{code}")
    assert final.json()["status"] == "已对账" and final.json()["extRef"] == "TLS-RECEIPT", final.text
    staged = await client.post(f"{API}/action-runs:stage", json=payload())
    assert staged.status_code == 201, staged.text
    confirmed = await client.post(f"{API}/action-runs/{staged.json()['id']}:confirm")
    assert confirmed.status_code == 200, confirmed.text
    pending = await session.scalar(select(WritebackCall).where(WritebackCall.msg == execution.QUEUED))
    monkeypatch.setattr(settings, "integration_ca_file", tmp_path / "missing-ca.pem")
    with patch.object(execution, "scoped_sessions", scope_of(session)):
        await execution.dispatch_one(pending.id, 1)
    await session.refresh(pending)
    assert pending.msg == "INTEGRATION_TLS_CONFIG_INVALID" and pending.attempt == 0
    assert len(https_receiver.requests) == 1
    with pytest.raises(AppError) as error:
        tls_verify()
    assert error.value.code == "INTEGRATION_TLS_CONFIG_INVALID"


async def test_http_guard_uses_only_current_receiver_and_exact_endpoint(https_receiver, request, monkeypatch):
    monkeypatch.delattr(request.node, "local_webhook_endpoint")
    monkeypatch.delattr(request.node, "local_webhook_methods")
    monkeypatch.setitem(request.node.funcargs, "receiver", https_receiver)
    context = ssl.create_default_context(cafile=str(https_receiver.ca_file))
    async with httpx.AsyncClient(trust_env=False, verify=context) as client:
        for url in (https_receiver.url + "/other", str(httpx.URL(https_receiver.url).copy_with(port=1))):
            with pytest.raises(AssertionError, match="External HTTP is forbidden"):
                await client.post(url)
        body = signing.encode_body({"guard": True})
        response = await client.post(
            https_receiver.url, content=body, headers=signing.signed_headers({}, body, SECRET.encode())
        )
        assert response.status_code == 200 and len(https_receiver.requests) == 1
        monkeypatch.delitem(request.node.funcargs, "receiver")
        with pytest.raises(AssertionError, match="External HTTP is forbidden"):
            await client.post(https_receiver.url)
