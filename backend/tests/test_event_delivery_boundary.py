"""Small, isolated checks retained for later; no database or external HTTP is used."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.action.condition_expr import Unavailable
from app.modules.event.delivery import check_order, delivery_key, http_target, object_order_lock


def test_explicit_test_endpoint_and_stable_subscription_idempotency():
    event = SimpleNamespace(code="event-a")
    assert delivery_key(event, "subscriber-a") == delivery_key(event, "subscriber-a")
    assert delivery_key(event, "subscriber-a") != delivery_key(event, "subscriber-b")
    settings = SimpleNamespace(event_delivery_http_enabled=True, env="dev")
    route = {"url": "https://production.invalid/events", "idempotent": True}
    with patch("app.modules.event.delivery.get_settings", return_value=settings):
        for invalid in (
            route,
            {**route, "test_url": route["url"]},
            {**route, "test_url": "http://external.invalid/events"},
            {**route, "test_url": "https://sandbox.invalid/events?token=unsafe"},
        ):
            try:
                http_target(invalid, True)
            except Unavailable:
                pass
            else:
                raise AssertionError("Unsafe or missing test channel was accepted")
        assert http_target({**route, "test_url": "http://127.0.0.1:9000/test"}, True).endswith("/test")
        settings.event_delivery_http_enabled = False
        try:
            http_target(route, False)
        except Unavailable:
            pass
        else:
            raise AssertionError("Disabled HTTP delivery was accepted")


async def test_ordered_delivery_blocks_unresolved_predecessors_and_quarantines_late_events():
    from datetime import UTC, datetime

    event = SimpleNamespace(
        event_type_id=1,
        obj_kind="order",
        object_id="order-a",
        occurred_at=datetime(2026, 10, 27, tzinfo=UTC),
        code="event-b",
    )
    delivery = SimpleNamespace(state="pending", error=None, result=None, attempts=0)
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), execute=AsyncMock())
    session.execute.return_value = [("event-a", {"subscribers": [{"status": "启用"}]}, 1)]
    try:
        await check_order(session, event, delivery, {"ordering": "object"})
    except Unavailable as exc:
        assert str(exc) == "ORDERING_PREDECESSOR_UNRESOLVED"
        assert delivery.result == {"blockedByEventId": "event-a"}
        assert delivery.state == "pending" and delivery.attempts == 0
    else:
        raise AssertionError("An unresolved predecessor was bypassed")
    # A missing delivery for an accepted predecessor must block before discover has run.
    session.execute.return_value = [("event-a", {"subscribers": [{"status": "启用"}]}, None)]
    try:
        await check_order(session, event, delivery, {"ordering": "object"})
    except Unavailable:
        pass
    else:
        raise AssertionError("An undiscovered predecessor was bypassed")
    session.execute.return_value = []
    assert await check_order(session, event, delivery, {"ordering": "object"})
    session.scalar.return_value = "already-claimed-later-event"
    assert not await check_order(session, event, delivery, {"ordering": "object"})
    assert delivery.state == "quarantined" and delivery.attempts == 0
    assert delivery.error == "ORDERING_LATE_EVENT_QUARANTINED"
    assert object_order_lock(1, "order", "a:b") != object_order_lock(1, "order:a", "b")
