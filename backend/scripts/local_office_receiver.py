"""Dedicated workspace-16 synthetic receipt service; no database or business writes.

Prepare: python -m scripts.local_office_receiver --prepare
Serve: python -m scripts.local_office_receiver --directory <private temporary directory>
Explicit rejection: add --outcome FAILED (default SUCCESS)
Check: python -m scripts.local_office_receiver --self-check
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts.local_webhook_receiver import MAX_BODY, private_directory, prepare, tls_context
from app.modules.integration.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, encode_body, resolve_secret, signature

HOST, PORT = "127.0.0.1", 8767
MARKER = "office-check-20261101"


class Receiver:
    def __init__(self, directory, outcome="SUCCESS"):
        if outcome not in ("SUCCESS", "FAILED"):
            raise ValueError("Outcome must be SUCCESS or FAILED")
        self.outcome = outcome
        self.directory = directory
        self.secret = resolve_secret((directory / "signing-secret.txt").read_text("utf-8"))
        self.token = (directory / "bearer-token.txt").read_text("utf-8")
        self.ledger = directory / "office-receipts.json"
        self.receipts = json.loads(self.ledger.read_text("utf-8")) if self.ledger.exists() else {}
        if not isinstance(self.receipts, dict):
            raise ValueError("Invalid existing receipt ledger")

    def authorized(self, headers):
        return headers.get("X-Ontology-Id") == "16" and hmac.compare_digest(headers.get("Authorization", ""), "Bearer " + self.token)

    def accept(self, body, headers):
        if not self.authorized(headers):
            return 401, {"error": "UNAUTHORIZED"}
        stamp = headers.get(TIMESTAMP_HEADER, "")
        try:
            valid = abs(time.time() - int(stamp)) <= 300 and hmac.compare_digest(signature(self.secret, stamp, body), headers.get(SIGNATURE_HEADER, ""))
        except ValueError:
            valid = False
        if not valid:
            return 401, {"error": "INVALID_SIGNATURE"}
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            return 400, {"error": "INVALID_JSON"}
        key = headers.get("Idempotency-Key", "")
        if (
            not isinstance(value, dict) or set(value) != {"ontologyId", "actionRunId", "actionType", "idemKey", "objKind", "objId", "score", "code"}
            or type(value["ontologyId"]) is not int or value["ontologyId"] != 16
            or value["actionType"] != "recordExternalInspection" or value["objKind"] != "OfficeDevice" or value["objId"] != "16002"
            or value["code"] != MARKER or type(value["score"]) is not int or not 0 <= value["score"] <= 100
            or not isinstance(value["actionRunId"], str) or not value["actionRunId"].startswith("ACT-")
            or not key.startswith("ont-16-act-") or len(key) > 128 or value["idemKey"] != key
        ):
            return 422, {"error": "UNEXPECTED_SYNTHETIC_ACTION"}
        digest = hashlib.sha256(body).hexdigest()
        previous = self.receipts.get(key)
        if previous and previous["bodyHash"] != digest:
            return 409, {"error": "IDEMPOTENCY_CONFLICT"}
        receipt = previous or {
            "bodyHash": digest, "receiptId": "OFFICE-" + hashlib.sha256(key.encode()).hexdigest()[:20],
            "payload": value, "receivedAt": int(time.time()), "status": self.outcome,
            "reason": "本轮合成接收器明确拒绝办公巡检，未执行业务写入" if self.outcome == "FAILED" else None,
            "resultCode": "OFFICE_SYNTHETIC_REJECTED" if self.outcome == "FAILED" else None,
        }
        receipt = {**receipt, "postCount": receipt.get("postCount", 0) + 1}
        updated = {**self.receipts, key: receipt}
        temporary = self.ledger.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(updated, output, ensure_ascii=True, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, self.ledger)
        self.receipts = updated
        result = {"status": receipt.get("status", "SUCCESS"), "receiptId": receipt["receiptId"]}
        if result["status"] == "FAILED":
            result.update(reason=receipt["reason"], code=receipt["resultCode"])
        return 200, result

    def query(self, key, headers):
        if not self.authorized(headers):
            return 401, {"error": "UNAUTHORIZED"}
        receipt = self.receipts.get(key)
        status = "查无" if not receipt else "查到·未执行" if receipt.get("status") == "FAILED" else "查到·成功"
        return 200, {"extStatus": status, "extRef": receipt["receiptId"] if receipt else None}


def serve(directory, outcome="SUCCESS"):
    private_directory(directory)
    receiver = Receiver(directory, outcome)

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, value):
            body = encode_body(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.connection.settimeout(5)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.path != "/office/receipt":
                    return self.respond(404, {"error": "NOT_FOUND"})
                if self.headers.get("Transfer-Encoding") or not 0 < size <= MAX_BODY:
                    return self.respond(413, {"error": "INVALID_BODY_LENGTH"})
                body = self.rfile.read(size)
                return self.respond(*(receiver.accept(body, self.headers) if len(body) == size else (400, {"error": "INCOMPLETE_BODY"})))
            except (OSError, ValueError):
                self.respond(503, {"error": "RECEIPT_NOT_PERSISTED"})

        def do_GET(self):
            self.connection.settimeout(5)
            url = urlsplit(self.path)
            if url.path == "/office/health" and not url.query:
                return self.respond(200, {"status": "ready", "ontologyId": 16})
            args = parse_qs(url.query)
            if url.path != "/office/status" or set(args) != {"idemKey"} or len(args["idemKey"]) != 1:
                return self.respond(404, {"error": "NOT_FOUND"})
            self.respond(*receiver.query(args["idemKey"][0], self.headers))

        def log_message(self, *_args):
            pass

    with HTTPServer((HOST, PORT), Handler) as server:
        server.socket = tls_context(directory).wrap_socket(server.socket, server_side=True)
        print(json.dumps({"listening": f"https://{HOST}:{PORT}", "ontologyId": 16, "outcome": outcome}), flush=True)
        server.serve_forever()


def self_check():
    with tempfile.TemporaryDirectory(prefix="rengu-webhook-office-check-") as name:
        directory = Path(name)
        (directory / "signing-secret.txt").write_text("local-check-secret", "utf-8")
        (directory / "bearer-token.txt").write_text("local-check-token", "utf-8")
        receiver = Receiver(directory)
        key = "ont-16-act-local-check"
        payload = {"ontologyId": 16, "actionRunId": "ACT-LOCAL-CHECK", "actionType": "recordExternalInspection", "idemKey": key, "objKind": "OfficeDevice", "objId": "16002", "score": 90, "code": MARKER}
        def send(value, **overrides):
            body = encode_body(value)
            stamp = str(int(time.time()))
            headers = {"X-Ontology-Id": "16", "Authorization": "Bearer local-check-token", "Idempotency-Key": key, TIMESTAMP_HEADER: stamp, SIGNATURE_HEADER: signature(receiver.secret, stamp, body), **overrides}
            return receiver.accept(body, headers)
        assert send(payload, Authorization="Bearer wrong")[0] == 401
        assert send(payload, **{SIGNATURE_HEADER: "invalid"})[0] == 401
        for changed in ({"ontologyId": 1}, {"objId": "16001"}, {"actionType": "other"}, {"score": True}, {"code": "other"}):
            assert send({**payload, **changed})[0] == 422
        first = send(payload)
        assert first[0] == 200 and send(payload) == first
        assert send({**payload, "score": 91})[0] == 409
        assert len(receiver.receipts) == 1 and Receiver(directory).receipts[key]["postCount"] == 2
        assert receiver.query(key, {"X-Ontology-Id": "16", "Authorization": "Bearer local-check-token"})[1]["extRef"] == first[1]["receiptId"]
        original_key, original_payload = key, payload
        receiver = Receiver(directory, "FAILED")
        assert send(original_payload) == first, "Mode changes must not rewrite an existing idempotent result"
        key = "ont-16-act-local-rejected"
        payload = {**payload, "idemKey": key, "actionRunId": "ACT-LOCAL-REJECTED"}
        assert send(payload, Authorization="Bearer wrong")[0] == 401
        assert send(payload, **{SIGNATURE_HEADER: "invalid"})[0] == 401
        assert send({**payload, "objId": "16001"})[0] == 422
        failed = send(payload)
        assert failed[0] == 200 and failed[1]["status"] == "FAILED"
        assert failed[1]["reason"] and failed[1]["code"] == "OFFICE_SYNTHETIC_REJECTED" and failed[1]["receiptId"]
        assert send(payload) == failed
        reopened = Receiver(directory)
        assert reopened.receipts[key]["postCount"] == 2 and len(reopened.receipts) == 2
        headers = {"X-Ontology-Id": "16", "Authorization": "Bearer local-check-token"}
        assert reopened.query(key, headers)[1] == {"extStatus": "查到·未执行", "extRef": failed[1]["receiptId"]}
        assert reopened.query(original_key, headers)[1]["extStatus"] == "查到·成功"
    print("Office receipt checks passed; no sockets or business writes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--directory", type=Path)
    mode.add_argument("--self-check", action="store_true")
    parser.add_argument("--outcome", choices=("SUCCESS", "FAILED"), default="SUCCESS")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.prepare:
        directory = Path(tempfile.mkdtemp(prefix="rengu-webhook-office-"))
        prepare(directory, ca_common_name=f"Rengu office workspace16 {secrets.token_hex(8)} CA")
        (directory / "bearer-token.txt").write_text(secrets.token_hex(32), "utf-8")
        print(json.dumps({"directory": str(directory), "caFile": str(directory / "ca.pem"), "origin": f"https://{HOST}:{PORT}"}))
    else:
        serve(args.directory, args.outcome)
