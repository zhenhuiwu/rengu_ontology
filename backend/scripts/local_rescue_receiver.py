"""仅本机演示：接收已验签的搜救事件，写入独立 SQLite 工单并返回回执。"""

import argparse
import hashlib
import hmac
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.modules.integration.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, encode_body, signature
from scripts.local_webhook_receiver import HOST, PORT, MAX_BODY, private_directory, tls_context


def serve(directory: Path):
    private_directory(directory)
    secret = (directory / "signing-secret.txt").read_bytes()
    allowed = json.loads((directory / "receiver-allowlist.json").read_text(encoding="utf-8"))
    if not isinstance(allowed, list) or not allowed:
        raise ValueError("An explicit local receiver allowlist is required")
    db = sqlite3.connect(directory / "rescue-receiver.sqlite3")
    db.execute(
        "CREATE TABLE IF NOT EXISTS tickets (idem_key TEXT PRIMARY KEY, body_hash TEXT NOT NULL, receipt TEXT NOT NULL, incident TEXT NOT NULL, action_run TEXT NOT NULL)"
    )
    db.commit()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, payload):
            data = encode_body(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.connection.settimeout(5)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    self.path != "/hook"
                    or self.headers.get("Transfer-Encoding")
                    or not 0 < length <= MAX_BODY
                ):
                    return self.respond(400, {"error": "INVALID_REQUEST"})
                body = self.rfile.read(length)
                timestamp = self.headers.get(TIMESTAMP_HEADER, "")
                if (
                    len(body) != length
                    or abs(time.time() - int(timestamp)) > 300
                    or not hmac.compare_digest(
                        signature(secret, timestamp, body), self.headers.get(SIGNATURE_HEADER, "")
                    )
                ):
                    return self.respond(401, {"error": "INVALID_SIGNATURE"})
                payload = json.loads(body)
                key = self.headers.get("Idempotency-Key", "")
                if (
                    not isinstance(payload, dict)
                    or not any(isinstance(item, dict) and payload.get("ontologyId") == item.get("ontologyId")
                               and payload.get("actionType") == item.get("actionType")
                               and str(payload.get("objId", "")) in item.get("objectIds", []) for item in allowed)
                    or payload.get("idemKey") != key
                    or not key
                    or not str(payload.get("actionRunId", "")).startswith("ACT-")
                ):
                    return self.respond(422, {"error": "UNEXPECTED_ACTION"})
                digest = hashlib.sha256(body).hexdigest()
                row = db.execute(
                    "SELECT body_hash, receipt FROM tickets WHERE idem_key = ?", (key,)
                ).fetchone()
                if row and row[0] != digest:
                    return self.respond(409, {"error": "IDEMPOTENCY_CONFLICT"})
                receipt = row[1] if row else "SAR-" + hashlib.sha256(key.encode()).hexdigest()[:20]
                if not row:
                    db.execute(
                        "INSERT INTO tickets VALUES (?, ?, ?, ?, ?)",
                        (key, digest, receipt, payload["objId"], payload["actionRunId"]),
                    )
                    db.commit()
                return self.respond(200, {"status": "SUCCESS", "receiptId": receipt})
            except (ValueError, TypeError, UnicodeDecodeError, sqlite3.Error, OSError):
                return self.respond(400, {"error": "REQUEST_FAILED"})

        def do_GET(self):
            request = urlsplit(self.path)
            key = parse_qs(request.query).get("idemKey", [""])[0]
            if request.path != "/status" or not key:
                return self.respond(400, {"error": "INVALID_REQUEST"})
            row = db.execute("SELECT receipt FROM tickets WHERE idem_key = ?", (key,)).fetchone()
            return self.respond(
                200, {"extStatus": "查到·成功", "extRef": row[0]} if row else {"extStatus": "查无"}
            )

        def log_message(self, *_args):
            pass

    with HTTPServer((HOST, PORT), Handler) as server:
        server.socket = tls_context(directory).wrap_socket(server.socket, server_side=True)
        print("Local rescue receiver listening on 127.0.0.1:8443", flush=True)
        try:
            server.serve_forever()
        finally:
            db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    serve(parser.parse_args().directory)
