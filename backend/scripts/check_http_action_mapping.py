"""Manual contract check for the HTTP action mapping; run from backend when needed."""
from app.core.errors import AppError
from app.modules.integration.http_mapping import request_body, response_body, validate_mapping

mapping = {"request": [{"target": "ticket", "source": "objId"}, {"target": "priority", "source": "params.level"}], "response": {"status": "state", "receiptId": "ticketId", "reason": "", "code": "", "success": "ok", "failed": "error", "unknown": "pending"}}
validate_mapping(mapping, {"level"})
assert request_body(mapping, {"objId": "SAR-1", "params": {"level": 2}}) == {"ticket": "SAR-1", "priority": 2}
assert response_body(mapping, {"state": "ok", "ticketId": "T1"})["status"] == "SUCCESS"
try:
    response_body(mapping, {"state": "unexpected"})
except ValueError:
    pass
else:
    raise AssertionError("unknown status accepted")
try:
    validate_mapping(mapping, set())
except AppError:
    pass
else:
    raise AssertionError("unconfigured parameter accepted")
