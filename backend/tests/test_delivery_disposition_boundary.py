"""Retained decision boundary check; no DB, worker or HTTP required."""

from types import SimpleNamespace

from app.core.errors import Conflict
from app.modules.event.exceptions import validate_decision


def test_manual_review_never_retries_late_completed_or_structurally_invalid_delivery():
    for state, error, attempts, decision in (
        ("quarantined", "ORDERING_LATE_EVENT_QUARANTINED", 0, "supplement"),
        ("quarantined", "ORDERING_LATE_EVENT_QUARANTINED", 0, "retry"),
        ("confirmed", None, 1, "retry"),
        ("processing", None, 1, "skip"),
        ("failed", "FILTER_NOT_EXECUTABLE", 0, "retry"),
        ("pending", "ORDERING_PREDECESSOR_UNRESOLVED", 0, "supplement"),
        ("pending", None, 0, "received"),
    ):
        row = SimpleNamespace(state=state, error=error, attempts=attempts)
        try:
            validate_decision(row, decision)
        except Conflict:
            pass
        else:
            raise AssertionError(f"Unsafe disposition allowed: {state}/{error}/{decision}")
    validate_decision(
        SimpleNamespace(state="unknown", error="HTTP_RESULT_UNKNOWN_REQUIRES_REVIEW", attempts=1), "retry"
    )
    validate_decision(
        SimpleNamespace(state="quarantined", error="ORDERING_LATE_EVENT_QUARANTINED", attempts=0), "skip"
    )
