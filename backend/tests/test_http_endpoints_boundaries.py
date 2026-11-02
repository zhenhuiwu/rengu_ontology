"""Offline checks for endpoint binding and bounded request values."""
from types import SimpleNamespace
import pytest
from app.core.errors import AppError
from app.modules.integration.http_endpoints import selected_endpoint
from app.modules.integration.http_mapping import request_body, validate_mapping


def test_endpoint_binding_and_request_values():
    mapping = {"request": [{"target": "state", "source": "object.status"}, {"target": "priority", "value": 1}], "response": {"status": "status", "receiptId": "receiptId", "reason": "", "code": "", "success": "ok", "failed": "failed", "unknown": "unknown"}}
    validate_mapping(mapping, set())
    system = SimpleNamespace(http_endpoints=[{"id": "update", "name": "Update", "purpose": "action", "method": "PATCH", "path": "/asset", "httpMapping": mapping}])
    assert selected_endpoint(system, "update", "PATCH /asset", mapping) is not None
    assert selected_endpoint(system, "update", "POST /asset", mapping) is None
    assert request_body(mapping, {}, {"status": "open"}) == {"state": "open", "priority": 1}
    with pytest.raises(AppError):
        request_body(mapping, {}, {})
