"""Local-only signed HTTPS demo receiver; never connects to a database or model.

Prepare: python -m scripts.local_webhook_receiver --prepare
Serve:   python -m scripts.local_webhook_receiver --directory <returned directory>
Check:   python -m scripts.local_webhook_receiver --self-check (no sockets)
"""

import argparse
import csv
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import ssl
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.modules.integration.signing import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    encode_body,
    resolve_secret,
    signature,
)

HOST, PORT = "127.0.0.1", 8443
MAX_BODY = 65_536


def private_directory(directory: Path) -> None:
    if directory.resolve() != (Path(__file__).resolve().parents[1] / ".local-rescue").resolve() and (
        directory.resolve().parent != Path(tempfile.gettempdir()).resolve() or not directory.name.startswith("rengu-webhook-")
    ):
        raise ValueError("Receiver material must stay in its dedicated temporary directory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
            errors="replace",
        )
        sid = next(csv.reader(identity.stdout.strip().splitlines()))[1]
        subprocess.run(
            ["icacls", str(directory), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
            check=True,
            capture_output=True,
        )
    else:
        directory.chmod(0o700)


def prepare(directory: Path) -> None:
    private_directory(directory)
    ca_key, server_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    now = datetime.fromtimestamp(time.time(), UTC)  # TLS validity uses the host clock.
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Rengu temporary local demo CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .sign(ca_key, hashes.SHA256())
    )
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(HOST))]), critical=False
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    material = {
        "ca.pem": ca.public_bytes(serialization.Encoding.PEM),
        "server.pem": server.public_bytes(serialization.Encoding.PEM),
        "server.key": server_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ),
        "signing-secret.txt": secrets.token_hex(32).encode("ascii"),
    }
    for name, data in material.items():
        with (directory / name).open("xb") as output:
            output.write(data)
        if os.name != "nt":
            (directory / name).chmod(0o600)


class Receiver:
    def __init__(self, directory: Path):
        self.directory = directory
        raw = os.environ.get("RENGU_LOCAL_WEBHOOK_SECRET") or (directory / "signing-secret.txt").read_text(
            "utf-8"
        )
        self.secret = resolve_secret(raw)
        self.ledger = directory / "receipts.json"
        self.receipts = json.loads(self.ledger.read_text("utf-8")) if self.ledger.exists() else {}
        if not isinstance(self.receipts, dict):
            raise ValueError("Invalid receipt ledger; existing evidence was not reset")

    def accept(self, body: bytes, headers) -> tuple[int, dict]:
        try:
            timestamp = headers.get(TIMESTAMP_HEADER, "")
            valid = abs(time.time() - int(timestamp)) <= 300 and hmac.compare_digest(
                signature(self.secret, timestamp, body), headers.get(SIGNATURE_HEADER, "")
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            return 401, {"error": "INVALID_SIGNATURE"}
        key = headers.get("Idempotency-Key", "")
        if not key or len(key) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            return 400, {"error": "INVALID_IDEMPOTENCY_KEY"}
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, {"error": "INVALID_JSON"}
        if (
            not isinstance(payload, dict)
            or type(payload.get("ontologyId")) is not int
            or payload["ontologyId"] != 1
            or payload.get("actionType") != "requestReplenishment"
            or payload.get("idemKey") != key
            or not isinstance(payload.get("actionRunId"), str)
            or not payload["actionRunId"].startswith("ACT-")
            or not isinstance(payload.get("declaredEdits"), list)
            or not payload["declaredEdits"]
        ):
            return 422, {"error": "UNEXPECTED_DEMO_ACTION"}
        digest = hashlib.sha256(body).hexdigest()
        previous = self.receipts.get(key)
        if previous:
            if previous["bodyHash"] != digest:
                return 409, {"error": "IDEMPOTENCY_CONFLICT"}
            return 200, {"status": "SUCCESS", "receiptId": previous["receiptId"]}
        receipt = {
            "bodyHash": digest,
            "receiptId": "LOCAL-" + hashlib.sha256(key.encode()).hexdigest()[:24],
            "actionRunId": payload["actionRunId"],
            "ontologyId": 1,
            "actionType": payload["actionType"],
            "declaredEdits": payload["declaredEdits"],
            "verified": True,
            "receivedAt": datetime.fromtimestamp(time.time(), UTC).isoformat(),
        }
        updated = {**self.receipts, key: receipt}
        temporary = self.ledger.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(updated, output, ensure_ascii=True, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.ledger)
        self.receipts = updated
        return 200, {"status": "SUCCESS", "receiptId": receipt["receiptId"]}


def tls_context(directory: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(directory / "server.pem", directory / "server.key")
    return context


def serve(directory: Path) -> None:
    private_directory(directory)
    receiver = Receiver(directory)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(5)
            status, result = 400, {"error": "INVALID_REQUEST"}
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/hook":
                    status, result = 404, {"error": "NOT_FOUND"}
                elif self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY:
                    status, result = 413, {"error": "INVALID_BODY_LENGTH"}
                else:
                    body = self.rfile.read(length)
                    if len(body) == length:
                        status, result = receiver.accept(body, self.headers)
            except (ValueError, UnicodeError):
                pass
            except OSError:
                status, result = 503, {"error": "RECEIPT_NOT_PERSISTED"}
            raw = encode_body(result)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass  # Request headers/body and credentials never enter HTTP logs.

    with HTTPServer((HOST, PORT), Handler) as server:
        server.socket = tls_context(directory).wrap_socket(server.socket, server_side=True)
        print(
            json.dumps({"listening": f"https://{HOST}:{PORT}/hook", "caFile": str(directory / "ca.pem")}),
            flush=True,
        )
        server.serve_forever()


def self_check() -> None:
    from app.core import clock

    with tempfile.TemporaryDirectory(prefix="rengu-webhook-check-") as temporary:
        directory = Path(temporary)
        prepare(directory)
        tls_context(directory)
        server = x509.load_pem_x509_certificate((directory / "server.pem").read_bytes())
        ca = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
        server.verify_directly_issued_by(ca)
        assert server.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.IPAddress) == [ipaddress.ip_address(HOST)]
        receiver = Receiver(directory)
        body = encode_body(
            {
                "ontologyId": 1,
                "actionType": "requestReplenishment",
                "actionRunId": "ACT-LOCAL-CHECK",
                "idemKey": "local-check",
                "declaredEdits": [{"kind": "编辑", "status": "external/declared"}],
            }
        )
        timestamp = str(int(time.time()))
        headers = {
            TIMESTAMP_HEADER: timestamp,
            SIGNATURE_HEADER: signature(receiver.secret, timestamp, body),
            "Idempotency-Key": "local-check",
        }
        assert receiver.accept(body, {**headers, SIGNATURE_HEADER: "sha256=invalid"})[0] == 401
        stale = str(int(timestamp) - 301)
        assert (
            receiver.accept(
                body,
                {
                    **headers,
                    TIMESTAMP_HEADER: stale,
                    SIGNATURE_HEADER: signature(receiver.secret, stale, body),
                },
            )[0]
            == 401
        )
        assert receiver.accept(body, {**headers, "Idempotency-Key": ""})[0] == 400
        first = receiver.accept(body, headers)
        assert first[0] == 200 and first[1]["status"] == "SUCCESS"
        assert Receiver(directory).accept(body, headers) == first
        with clock.frozen(datetime.fromtimestamp(int(stale), UTC)):
            assert receiver.accept(body, headers) == first
            assert (
                receiver.accept(
                    body,
                    {
                        **headers,
                        TIMESTAMP_HEADER: stale,
                        SIGNATURE_HEADER: signature(receiver.secret, stale, body),
                    },
                )[0]
                == 401
            )
        changed = body + b" "
        assert (
            receiver.accept(
                changed, {**headers, SIGNATURE_HEADER: signature(receiver.secret, timestamp, changed)}
            )[0]
            == 409
        )
        assert len(receiver.receipts) == 1
    print(
        "Local checks passed: certificate chain/IP SAN, HMAC/freshness, key validation, "
        "durable idempotency; sockets opened: 0"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--directory", type=Path)
    mode.add_argument("--self-check", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_check:
        self_check()
    elif arguments.prepare:
        path = Path(tempfile.mkdtemp(prefix="rengu-webhook-"))
        prepare(path)
        print(json.dumps({"directory": str(path), "caFile": str(path / "ca.pem")}, ensure_ascii=True))
    else:
        serve(arguments.directory)
