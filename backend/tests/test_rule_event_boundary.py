"""Pure boundary check only; no DB, business writes or test events."""

from types import SimpleNamespace

from app.core.clock import now
from app.core.errors import AppError
from app.modules.dynamic.event_rule_runtime import EventExpression, dedupe_spec
from app.modules.dynamic.rule_runtime import parse_expression


def test_retained_event_rule_boundaries():
    definition = {"objKind": "order", "objectApi": "SalesOrder", "object": "订单"}
    fields = {"fromStatus": "原状态", "toStatus": "新状态"}
    snapshot = {
        "lifecycles": {
            "order": {
                "version": 4,
                "states": [{"key": "待支付"}, {"key": "已支付"}],
                "transitions": [{"from": "待支付", "to": "已支付"}],
            }
        }
    }
    source = '(event.fromStatus, event.toStatus) NOT IN lifecycle("SalesOrder", 4).transitions'
    expression = EventExpression(parse_expression(source), fields, snapshot, definition)
    assert expression.run({"fromStatus": "待支付", "toStatus": "已支付"}, now())[0] is False
    assert expression.run({"fromStatus": "已支付", "toStatus": "待支付"}, now())[0] is True
    try:
        EventExpression(parse_expression(source), fields, {}, definition)
    except AppError:
        pass
    else:
        raise AssertionError("Missing lifecycle snapshot must not borrow current state")
    rule = SimpleNamespace(code="SM-ORD", dedupe="事件 ID", cooldown="不重复")
    event = SimpleNamespace(code="EV-real", object_id="ORDER-1", definition_snapshot=definition, payload={})
    key, window = dedupe_spec(rule, event, [])
    assert window is None and key == dedupe_spec(rule, event, [])[0]
    rule.dedupe = "订单摘要"
    try:
        dedupe_spec(rule, event, [])
    except AppError:
        pass
    else:
        raise AssertionError("Undeclared dedupe identity must not be inferred")
