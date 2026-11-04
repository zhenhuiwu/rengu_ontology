"""Loopback-only synthetic pure function. No business data, writes, or external requests."""

import argparse
import contextlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--ontology-id", type=int, default=17)
    parser.add_argument("--revision", default="fixture-1")
    parser.add_argument("--token-file", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.ontology_id < 1:
        parser.error("port/ontology-id invalid")
    try:
        token = (args.token_file.read_text(encoding="utf-8").strip() if args.token_file
                 else os.environ.get("RENGU_FUNCTION_FIXTURE_TOKEN", ""))
    except OSError:
        parser.error("private token file unavailable")
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        parser.error("set RENGU_FUNCTION_FIXTURE_TOKEN or a private --token-file")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log credentials, request bodies, or query strings.

        def reply(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(raw)

        def do_POST(self):
            if self.path != "/calculate":
                self.reply(404, {"code": "NOT_FOUND"})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                self.reply(401, {"code": "UNAUTHORIZED"})
                return
            if self.headers.get("X-Ontology-Id") != str(args.ontology_id):
                self.reply(403, {"code": "ONTOLOGY_MISMATCH"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError
                body = json.loads(self.rfile.read(length))
                if (not isinstance(body, dict)
                    or set(body) != {"functionId", "version", "revision", "input"}
                    or body["revision"] != args.revision
                    or not all(isinstance(body[key], str) and 1 <= len(body[key]) <= 64
                               for key in ("functionId", "version"))
                    or not isinstance(body["input"], dict) or set(body["input"]) != {"value"}
                    or type(body["input"]["value"]) is not int
                    or body["input"]["value"].bit_length() > 254):
                    raise ValueError
                result = {"result": body["input"]["value"] * 2}
            except (ValueError, KeyError, TypeError, RecursionError):
                self.reply(422, {"code": "FIXTURE_SCHEMA_INVALID"})
                return
            self.reply(200, {"revision": args.revision, "output": result})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"Pure synthetic function ready on 127.0.0.1:{args.port}; ontology={args.ontology_id}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
