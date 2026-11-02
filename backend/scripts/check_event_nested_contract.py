"""Local event contract checks: no database, worker, network or business fixtures.

Run from backend: .venv/Scripts/python.exe scripts/check_event_nested_contract.py
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.action.condition_expr import Unavailable, evaluate
from app.modules.dynamic.event_rule_runtime import EventExpression, dedupe_spec
from app.modules.dynamic.rule_runtime import parse_expression
from app.modules.event.delivery import check_order, preflight_failure, prepare
from app.modules.event.ingress import validate_fields
from app.modules.event.payload_schema import field_paths, payload_value
from app.modules.event.schemas import FieldOut
from app.modules.event.simulation import filter_matches


def check():
    fields = [FieldOut(name="读数", api="reading", type="对象", required=True, desc="", sample="{}", schema={"fields": [
        {"name": "值", "api": "value", "type": "数值", "required": True, "schema": None},
        {"name": "状态", "api": "active", "type": "布尔", "required": True, "schema": None},
    ]})]
    payload = {"reading": {"value": 4.5, "active": False}}
    validate_fields(fields, payload)
    assert field_paths(fields)["reading.value"] == "读数.值"
    assert field_paths(fields, typed=True)["reading.value"] == ("读数.值", "数值")
    assert payload_value(payload, "reading.value") == 4.5
    assert evaluate("true and not false", {})
    assert not evaluate("false", {})
    assert evaluate("载荷.reading.active = false", {("载荷", "reading.active"): False})
    assert evaluate('载荷.status = "false"', {("载荷", "status"): "false"})
    assert filter_matches("reading.value < 5 and reading.active = false", fields, payload)
    expression = EventExpression(parse_expression("event.reading.value < 5 and event.reading.active = false"), {}, {}, {"payload": [f.model_dump() for f in fields]})
    assert expression.run(payload, datetime.now(UTC))[0]
    historical = {"reading": {"value": 4.5, "active": False, "undeclared_secret": "never-expose"}}
    assert payload_value(historical, "reading", fields) == payload["reading"]
    assert filter_matches("reading != 空", fields, historical)
    parent = EventExpression(parse_expression("event.reading != None"), {}, {}, {"payload": [f.model_dump() for f in fields]})
    matched, description = parent.run(historical, datetime.now(UTC))
    assert matched and "undeclared_secret" not in description and "never-expose" not in description
    array_fields = [FieldOut(name="列表", api="rows", type="数组", required=True, desc="", sample="[]", schema={"item": {"type": "对象", "schema": {"fields": [
        {"name": "子项", "api": "child", "type": "对象", "required": True, "schema": fields[0].schema},
    ]}}})]
    array_payload = {"rows": [{"child": historical["reading"], "secret": "never-expose"}]}
    assert payload_value(array_payload, "rows", array_fields) == [{"child": payload["reading"]}]
    assert "never-expose" not in str(array_payload | {"rows": payload_value(array_payload, "rows", array_fields)})
    assert not expression.run({"reading": {"value": 6, "active": False}}, datetime.now(UTC))[0]
    assert not expression.run({"reading": {"value": 4.5, "active": True}}, datetime.now(UTC))[0]
    for run in (
        lambda: payload_value({}, "reading.value"),
        lambda: payload_value(historical, "reading"),
        lambda: payload_value(historical, "reading.undeclared_secret", fields),
        lambda: payload_value({"scalar": {"secret": "never-expose"}}, "scalar", [SimpleNamespace(api="scalar", type="字符串", schema=None)]),
        lambda: filter_matches("reading.value < 5", fields, {}),
        lambda: expression.run({"reading": {"active": False}, "reading.value": 0}, datetime.now(UTC)),
        lambda: EventExpression(parse_expression("event.reading.undeclared < 5"), {}, {}, {"payload": [f.model_dump() for f in fields]}),
    ):
        try:
            run()
        except (KeyError, Unavailable):
            pass
        except Exception as exc:
            assert getattr(exc, "code", None) == "RULE_EVALUATION_UNAVAILABLE"
        else:
            raise AssertionError("Missing or undeclared payload value was accepted")
    rule = SimpleNamespace(dedupe="reading.value", code="rule-contract", ontology_id=1, cooldown="不重复")
    event = SimpleNamespace(payload=payload, definition_snapshot={}, object_id="local-object")
    assert dedupe_spec(rule, event, fields)[0].startswith("rule-")
    now = datetime.now(UTC)
    for error in ("NO_PERMISSION", "EVENT_OBJECT_FORBIDDEN", "DELIVERY_ROUTE_NOT_CONFIGURED"):
        row = SimpleNamespace(created_at=now)
        preflight_failure(row, error)
        assert row.state == "failed" and row.next_at is None
    row = SimpleNamespace(created_at=now - timedelta(minutes=16))
    preflight_failure(row, "EVENT_OBJECT_NOT_SYNCED")
    assert row.state == "failed" and row.next_at is None


async def check_ordering():
    event = SimpleNamespace(event_type_id=1, obj_kind="contract", object_id="one", occurred_at=datetime.now(UTC), code="local-event", definition_snapshot={"ordering": "object"})
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), execute=AsyncMock(return_value=[]))
    assert await check_order(session, event, SimpleNamespace(), {})
    session.scalar.return_value = "later-event"
    row = SimpleNamespace()
    assert not await check_order(session, event, row, {})
    assert row.state == "quarantined"
    session.scalar.return_value = None
    session.execute.return_value = [("earlier-event", {}, 1)]
    try:
        await check_order(session, event, SimpleNamespace(), {})
    except Unavailable as exc:
        assert str(exc) == "ORDERING_PREDECESSOR_UNRESOLVED"
    else:
        raise AssertionError("Unresolved predecessor was bypassed")
    for route, declared in (({"ordering": "none"}, "object"), ({}, "custom-dimension")):
        event.definition_snapshot["ordering"] = declared
        try:
            await check_order(session, event, SimpleNamespace(), route)
        except Unavailable as exc:
            assert str(exc) == "ORDERING_CHANNEL_NOT_CONFIGURED"
        else:
            raise AssertionError("Unsupported ordering was silently downgraded")

    for error in ("DELIVERY_ROUTE_NOT_CONFIGURED", "HTTP_ROUTE_INVALID", "NO_PERMISSION"):
        now = datetime.now(UTC)
        row = SimpleNamespace(id=1, event_id=2, state="pending", error=error, result=None, attempts=0, next_at=None, created_at=now)
        event = SimpleNamespace(id=2, is_test=True, ingested_at=now)
        session = SimpleNamespace(get=AsyncMock(side_effect=[row, event]), scalar=AsyncMock(return_value=row), scalars=AsyncMock(return_value=[row]), add=Mock())
        with patch("app.modules.event.delivery.resolved_route", new_callable=AsyncMock) as route:
            assert not await prepare(session, 1)
        route.assert_not_awaited()
        assert row.state == "failed" and row.error == error and row.attempts == 0 and row.next_at is None


if __name__ == "__main__":
    check()
    asyncio.run(check_ordering())
    print("Event nested payload, boolean and ordering contract checks passed (local only).")
