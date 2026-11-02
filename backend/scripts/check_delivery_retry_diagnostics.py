"""Pure delivery policy checks; no application imports, database, network or worker.

Run: .venv/Scripts/python.exe scripts/check_delivery_retry_diagnostics.py
"""

import ast
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


def definitions(path, names, scope):
    """Evaluate the actual pure definitions without importing application startup code."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    found = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
            found.add(node.name)
        if isinstance(node, ast.Assign):
            assigned = {target.id for target in node.targets if isinstance(target, ast.Name)}
            if assigned & names:
                nodes.append(node)
                found |= assigned
    assert names <= found, names - found
    selected = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(selected, str(path), "exec"), scope)


def check():
    root = Path(__file__).resolve().parents[1] / "app/modules"
    scope = {"re": re, "timedelta": timedelta, "Unavailable": RuntimeError}
    definitions(root / "event/delivery_history.py", {"failure_code", "safe_error_details"}, scope)
    definitions(root / "integration/signing.py", {"BACKOFF_BASE", "BACKOFF_CAP", "backoff"}, scope)
    definitions(root / "event/delivery.py", {
        "INTERVAL", "PREFLIGHT_WAIT_LIMIT", "PREFLIGHT_WAIT_ERRORS", "RETRY_SCHEDULED",
        "preflight_failure", "next_auto_attempt", "schedule_retry",
    }, scope)
    now = datetime(2026, 10, 31, tzinfo=UTC)
    scope["execution_now"] = lambda: now
    for error in ("NO_PERMISSION", "DELIVERY_ROUTE_NOT_CONFIGURED", *scope["PREFLIGHT_WAIT_ERRORS"]):
        row = SimpleNamespace(created_at=now, attempts=0)
        scope["preflight_failure"](row, error)
        assert row.state == ("pending" if error in scope["PREFLIGHT_WAIT_ERRORS"] else "failed")
        row.created_at = now - timedelta(minutes=15)
        scope["preflight_failure"](row, error)
        assert row.state == "failed" and row.next_at is None and row.attempts == 0
    for previous in (None, True, "1", 0, -1, 4):
        journal = SimpleNamespace(kind="attempt_started", attempt=3, detail={"autoAttempt": previous})
        try:
            scope["next_auto_attempt"](journal, 3, 3, "HTTP_RETRY_SCHEDULED", "same")
        except RuntimeError:
            pass
        else:
            raise AssertionError("A missing/corrupt retry count reset the budget")
    for journal in (None, SimpleNamespace(kind="disposition", attempt=1, detail={"autoAttempt": 1}),
                    SimpleNamespace(kind="attempt_started", attempt=1, detail={"autoAttempt": 1})):
        try:
            scope["next_auto_attempt"](journal, 2, 3, "HTTP_RETRY_SCHEDULED", "same")
        except RuntimeError:
            pass
        else:
            raise AssertionError("An unrelated journal allowed automatic replay")
    row = SimpleNamespace()
    for attempt in range(1, 5):
        assert scope["schedule_retry"](row, attempt, 3, "9999") == (attempt <= 3)
        if attempt <= 3:
            assert row.next_at <= now + timedelta(minutes=15)
            journal = SimpleNamespace(
                kind="attempt_started", attempt=attempt,
                detail={"autoAttempt": attempt, "routeFingerprint": "same"},
            )
            for error in ("HTTP_RETRY_SCHEDULED", *scope["PREFLIGHT_WAIT_ERRORS"]):
                assert scope["next_auto_attempt"](journal, attempt, 3, error, "same") == attempt + 1
    manual = SimpleNamespace(kind="disposition", attempt=3, detail={"decision": "retry"})
    assert scope["next_auto_attempt"](manual, 3, 3, None, "same") == 1
    for error, fingerprint in ((None, "same"), ("HTTP_RETRY_SCHEDULED", "changed")):
        try:
            scope["next_auto_attempt"](journal, 3, 3, error, fingerprint)
        except RuntimeError:
            pass
        else:
            raise AssertionError("An unaudited pending row or changed recipient allowed replay")
    assert not scope["schedule_retry"](row, 0, 3, None)
    error = RuntimeError("sensitive-input")
    error.code, error.status_code = "VALIDATION_FAILED", 422
    error.details = {
        "fields": [{"field": "params.value", "message": "sensitive-input", "input": "sensitive-input"},
                   {"field": "params.password", "message": "sensitive-input"}] * 30,
        "failed": [{"name": "对象状态校验", "fail": "sensitive-input"}, {"name": "https://sensitive-input"}],
        "payload": {"secret": "sensitive-input"},
    }
    details = scope["safe_error_details"](error, "execution")
    assert details["fieldsCount"] == 60 and len(details["fields"]) == 20
    assert details["fields"][0]["field"] == "params.value"
    assert "field" not in details["fields"][1]
    assert details["failed"][0]["name"] == "对象状态校验"
    assert "name" not in details["failed"][1] and "sensitive-input" not in json.dumps(details)
    assert scope["failure_code"](RuntimeError("sensitive-input")) == "DELIVERY_PRECONDITION_UNAVAILABLE"
    print("delivery retry/diagnostic pure checks: passed")


if __name__ == "__main__":
    check()
