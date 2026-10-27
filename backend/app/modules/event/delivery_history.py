"""Append-only delivery evidence, shared by the worker and human review."""

from app.modules.event.models import EventDeliveryRecord

RESULT_KEYS = (
    "httpStatus",
    "actionId",
    "ruleId",
    "ruleVersion",
    "hitId",
    "alertId",
    "status",
    "actionStatus",
    "actionReason",
    "blockedByEventId",
)


def safe_result(result):
    return {key: result[key] for key in RESULT_KEYS if key in result} if result else None


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
