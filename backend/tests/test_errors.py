from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import errors
from app.core.errors import (
    ExportForbidden,
    IdempotencyConflict,
    SubmissionCriteriaFailed,
    install_error_handlers,
)


def _client(exc: Exception) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/x")
    def boom():
        raise exc

    return TestClient(app)


def test_submission_criteria_failed():
    r = _client(SubmissionCriteriaFailed([{"name": "库存充足", "fail": "可用库存 0"}])).get("/x")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "SUBMISSION_CRITERIA_FAILED"
    assert body["details"] == {"failed": [{"name": "库存充足", "fail": "可用库存 0"}]}


def test_export_forbidden_and_idempotency_conflict():
    r = _client(ExportForbidden()).get("/x")
    assert (r.status_code, r.json()["code"]) == (403, "EXPORT_FORBIDDEN")
    r = _client(IdempotencyConflict(details={"idemKey": "act-1"})).get("/x")
    assert (r.status_code, r.json()["code"]) == (409, "IDEMPOTENCY_CONFLICT")


def test_no_action_blocked():
    codes = {getattr(c, "code", None) for c in vars(errors).values() if isinstance(c, type)}
    assert "ACTION_BLOCKED" not in codes
