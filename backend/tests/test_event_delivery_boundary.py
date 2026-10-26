"""Small, isolated checks retained for later; no database or external HTTP is used."""

from types import SimpleNamespace
from unittest.mock import patch

from app.modules.action.condition_expr import Unavailable
from app.modules.event.delivery import delivery_key, http_target


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
