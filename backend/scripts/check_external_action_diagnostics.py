"""Offline checks only. Run manually with PYTHONPATH=.; no DB or network calls.

This file is intentionally not run during the 2026-10-31 repair.
"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from app.core.errors import AppError
from app.modules.action.policy import parse
from app.modules.integration.execution import submit_adapter
from app.modules.integration.http_endpoints import HttpEndpointsIn, require_selected_endpoint, selected_endpoint
from app.modules.integration.schemas import ExtSystemDetailOut


def error_code(call, code):
    try:
        call()
    except AppError as error:
        assert error.code == code, error.code
        return error
    raise AssertionError(f"Expected {code}")


def main():
    mapping = {"request": [{"target": "target", "source": "params.target_id"}], "response": {"status": "status", "receiptId": "receipt", "reason": "", "code": "", "success": "ok", "failed": "failed", "unknown": "unknown"}}
    endpoint = {"id": "first", "name": "First", "purpose": "action", "method": "POST", "path": "/execute", "httpMapping": mapping}
    system = SimpleNamespace(code="CHECK", action_authorizations={"action": {"version": "v1", "path": "/execute"}}, http_endpoints=[endpoint], idempotent=True, idem_header="Idempotency-Key")
    definition = {"id": "action", "version": "v2", "httpEndpointId": "first", "endpoint": "POST /execute", "httpMapping": mapping, "params": [{"api": "target_id"}]}
    with patch("app.modules.integration.execution.get_adapter") as adapter:
        error = error_code(lambda: submit_adapter(system, definition), "ACTION_EXECUTOR_AUTHORIZATION_VERSION_MISMATCH")
        assert error.details["expectedVersion"] == "v2" and error.details["authorizedVersion"] == "v1"
        adapter.assert_not_called()
    config = SimpleNamespace(submit_paths=["/execute"], submit_path="/execute", actions=[])
    adapter = SimpleNamespace(config=config, path=lambda operation: config.submit_path)
    system.action_authorizations["action"]["version"] = "v2"
    with patch("app.modules.integration.execution.get_adapter", return_value=adapter):
        assert submit_adapter(system, definition) is adapter
        system.action_authorizations["action"]["path"] = "/not-permitted"
        error_code(lambda: submit_adapter(system, definition), "ACTION_EXECUTOR_AUTHORIZATION_PATH_MISMATCH")
        system.action_authorizations["action"]["path"] = "/execute"
        system.idem_header = "X-Idempotency-Key"
        error_code(lambda: submit_adapter(system, definition), "ACTION_EXECUTOR_UNAVAILABLE")
        system.idem_header = "Idempotency-Key"
    assert require_selected_endpoint(system, "first", "POST /execute", mapping, {"target_id"}).id == "first"
    assert selected_endpoint(system, "missing", "POST /execute", mapping) is None
    for endpoint_id, path, current_mapping, params, expected in [
        ("missing", "POST /execute", mapping, {"target_id"}, "NOT_FOUND"),
        ("first", "/execute", mapping, {"target_id"}, "FORMAT_INVALID"),
        ("first", "PATCH /execute", mapping, {"target_id"}, "CHANGED"),
        ("first", "POST /execute", mapping, set(), "PARAM_UNDEFINED"),
        ("first", "POST /execute", None, {"target_id"}, "MAPPING_CHANGED"),
    ]:
        error = error_code(lambda: require_selected_endpoint(system, endpoint_id, path, current_mapping, params), f"ACTION_HTTP_ENDPOINT_{expected}")
        assert error.details["fields"]
    changed = deepcopy(mapping)
    changed["request"] = [{"target": "target", "value": "DO_NOT_ECHO_REQUEST_VALUE"}]
    error = error_code(lambda: require_selected_endpoint(system, "first", "POST /execute", changed), "ACTION_HTTP_ENDPOINT_MAPPING_CHANGED")
    assert "DO_NOT_ECHO_REQUEST_VALUE" not in str(error.details)
    duplicate_route = {**endpoint, "id": "second", "name": "Second"}
    assert len(HttpEndpointsIn(version=1, endpoints=[endpoint, duplicate_route]).endpoints) == 2
    for daily in [{"dailyCount": 3}, {"dailyLimit": 3}, {"dailyCount": 3, "dailyLimit": 3}]:
        assert parse({"mode": "auto", "limits": daily}).dump()["limits"] == {"dailyCount": 3}
    for invalid in [{"dailyCount": 2, "dailyLimit": 3}, {"dailyCount": 2, "perMinute": 1}]:
        try:
            parse({"mode": "auto", "limits": invalid})
        except ValueError:
            pass
        else:
            raise AssertionError("Conflicting or unsupported limits accepted")
    detail = ExtSystemDetailOut.model_validate(dict(code="CHECK", name="Check", vendor="Check", category="", endpoint="https://invalid.example", auth_type="无需授权", auth_detail="", idempotent=True, idem_header="Idempotency-Key", timeout_ms=1000, max_retry=0, retry_text="", rate_limit="", owner="", status="正常", env="测试", lock_version=7))
    assert detail.model_dump(by_alias=True)["lockVersion"] == 7
    print("External action diagnostics: OK (offline)")


if __name__ == "__main__":
    main()
