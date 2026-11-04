"""Minimal MCP smoke test for the ontology platform (stdlib only).

Usage:
  set RENGU_API_KEY=rg_live_...
  set RENGU_ONTOLOGY_ID=6
  python docs/ai/mcp_smoke.py
  python docs/ai/mcp_smoke.py --ask "当前有哪些平台？"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def rpc(base: str, key: str, ontology: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base.rstrip('/')}/api/qa/v1/mcp",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "X-Ontology-Id": ontology,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("RENGU_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--ask", default="")
    args = parser.parse_args()
    key = os.environ.get("RENGU_API_KEY", "").strip()
    ontology = os.environ.get("RENGU_ONTOLOGY_ID", "6").strip()
    if not key:
        print("Set RENGU_API_KEY (rg_live_... or rg_test_...)", file=sys.stderr)
        return 2

    init = rpc(args.base, key, ontology, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "rengu-mcp-smoke", "version": "1"}},
    })
    print("initialize:", json.dumps(init.get("result", {}).get("serverInfo"), ensure_ascii=False))

    listed = rpc(args.base, key, ontology, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = listed.get("result", {}).get("tools", [])
    print("tools:", ", ".join(tool["name"] for tool in tools))
    if not any(tool["name"] == "qa_ask" for tool in tools):
        print("qa_ask is not available for this key/workspace", file=sys.stderr)
        return 1

    if args.ask:
        called = rpc(args.base, key, ontology, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "qa_ask", "arguments": {"question": args.ask}},
        })
        print(json.dumps(called, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
