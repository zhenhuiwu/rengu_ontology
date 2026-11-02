"""Append-only delivery evidence, shared by the worker and human review."""

import re

from app.modules.event.models import EventDeliveryRecord

RESULT_KEYS = (
    "httpStatus",
    "actionId",
    "ruleId",
    "ruleVersion",
    "hitId",
    "alertId",
    "status",
    "evaluationMode",
    "evaluatedAt",
    "actionStatus",
    "actionReason",
    "blockedByEventId",
)


def safe_result(result):
    return {key: result[key] for key in RESULT_KEYS if key in result} if isinstance(result, dict) else None


def failure_code(exc):
    code = getattr(exc, "code", str(exc))
    if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", code):
        return code
    return "DELIVERY_PRECONDITION_UNAVAILABLE"


def safe_error_details(exc, phase):
    """Keep diagnostic locations, never exception text, input values or arbitrary nested details."""
    result = {"code": failure_code(exc), "phase": phase, "valuesOmitted": True}
    status = getattr(exc, "status_code", None)
    if type(status) is int and 400 <= status <= 599:
        result["status"] = status
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return result
    for key, label, reason in (
        ("fields", "field", "FIELD_VALIDATION_FAILED"),
        ("failed", "name", "SUBMISSION_CRITERION_FAILED"),
    ):
        items = details.get(key)
        if not isinstance(items, list):
            continue
        result[f"{key}Count"] = len(items)
        result[key] = []
        for index, item in enumerate(items[:20]):
            entry = {"index": index, "reason": reason}
            value = item.get(label) if isinstance(item, dict) else None
            # Locations/names may be configurable; discard expressions, URLs and sensitive labels.
            if (
                isinstance(value, str) and re.fullmatch(r"[\w.\[\] -]{1,128}", value)
                and not re.search(r"token|secret|password|credential|authorization|cookie", value, re.I)
            ):
                entry[label] = value
            result[key].append(entry)
    return result


def record(session, delivery, kind, *, actor="internal:event", detail=None):
    session.add(
        EventDeliveryRecord(
            delivery_id=delivery.id,
            kind=kind,
            attempt=delivery.attempts,
            state=delivery.state,
            error=delivery.error,
            result=safe_result(delivery.result),
            detail=detail,
            created_by=actor,
        )
    )
