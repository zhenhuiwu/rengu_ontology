"""Small pure checks retained for later authorized execution; no business fixtures."""

import ast
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.modules.action.condition_expr import Unavailable
from app.modules.dynamic.metric_rules import MetricCalls, bucket_value
from app.modules.dynamic.rule_editor import compile_conditions
from app.modules.dynamic.scheduler import next_slot, slot_expired


def test_explicit_schedule_and_metric_buckets():
    tz = "Asia/Shanghai"
    at = datetime(2026, 10, 27, 9, tzinfo=ZoneInfo(tz))
    assert not slot_expired({}, at - timedelta(seconds=59), at)
    assert slot_expired({}, at - timedelta(minutes=1), at)
    assert not slot_expired({"catchupMinutes": 60}, at - timedelta(minutes=60), at)
    assert slot_expired({"catchupMinutes": 60}, at - timedelta(minutes=61), at)
    assert (
        next_slot({"kind": "定时", "cron": "0 8 * * *", "timezone": tz}, at).astimezone(ZoneInfo(tz)).day
        == 28
    )
    series = {"2026-10-26": "120", "2026-10-25": "110", "2026-10-19": "100"}
    assert bucket_value(series, at=at, timezone=tz, period="day", transform="value") == 120
    assert bucket_value(series, at=at, timezone=tz, period="day", transform="prev") == 110
    assert bucket_value(series, at=at, timezone=tz, period="day", transform="wow") == 0.2
    try:
        bucket_value({}, at=at, timezone=tz, period="day", transform="prev")
    except Unavailable:
        pass
    else:
        raise AssertionError("missing baseline must not become zero")
    text = compile_conditions([{"expression": "order.status", "op": "=", "value": "已出库"}], "且")
    assert ast.literal_eval(ast.parse(text, mode="eval").body.comparators[0]) == "已出库"
    obj = SimpleNamespace(code="order", api_name="SalesOrder", display_name="订单")
    expression = MetricCalls(obj).visit(ast.parse("prev(counts@v2(order, day))", mode="eval"))
    assert ast.unparse(expression) == "metric_value('counts', 2, 'day', 'prev')"
