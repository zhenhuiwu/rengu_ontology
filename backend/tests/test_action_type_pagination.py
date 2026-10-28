"""定义目录不加载所有子段，参数/载荷页和运行统计由数据库提供。"""

from datetime import timedelta

from sqlalchemy import event, select

from app.core.clock import local_now, now
from app.modules.dynamic.models import ActionRun, ActionStatus
from tests.dynamic_support import API, dyn  # noqa: F401


async def test_definition_pages_do_not_load_other_sections(dyn, client, engine):  # noqa: F811
    statements = []

    def captured(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", captured)
    try:
        for path, table in (("/action-types", "action_type"), ("/event-types", "event_type")):
            statements.clear()
            first = await client.get(API + path, params={"size": 2})
            assert first.status_code == 200, first.text
            assert any(f"FROM {table} " in sql and "LIMIT" in sql for sql in statements)
            for child in (
                "action_param",
                "submission_criterion",
                "ontology_edit_rule",
                "side_effect",
                "event_payload_field",
                "event_subscription",
            ):
                assert not any(f"FROM {child} " in sql or f"FROM {child}\n" in sql for sql in statements)
            second = (await client.get(API + path, params={"page": 2, "size": 2})).json()
            assert second["total"] == first.json()["total"] > 2
            assert not ({r["id"] for r in first.json()["items"]} & {r["id"] for r in second["items"]})
        for path, table in (
            ("/action-types/request_replenish/params", "action_param"),
            ("/event-types/order_status_changed/payload-fields", "event_payload_field"),
        ):
            complete = (await client.get(API + path, params={"size": 200})).json()
            statements.clear()
            second = await client.get(API + path, params={"page": 2, "size": 2})
            assert second.status_code == 200, second.text
            result = second.json()
            assert result["items"] == complete["items"][2:4] and result["total"] == complete["total"]
            assert any(f"FROM {table}" in sql and "LIMIT" in sql and "OFFSET" in sql for sql in statements)
            if "payload-fields" in path:
                assert result["envelopeCount"] == complete["envelopeCount"]
                assert any(row["envelope"] for row in complete["items"])
        default = (await client.get(API + "/action-types")).json()
        assert default["size"] == 10
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", captured)


async def test_type_run_statistics_are_complete(dyn, client):  # noqa: F811
    code = "request_replenish"
    rows = list(
        await dyn.scalars(
            select(ActionRun).where(
                ActionRun.action_type_code == code,
                ActionRun.in_center.is_(False),
                ActionRun.submitted_at >= now() - timedelta(days=30),
                ActionRun.submitted_at <= now(),
            )
        )
    )
    response = await client.get(API + f"/action-types/{code}/stats")
    assert response.status_code == 200, response.text
    stats = response.json()

    def count(states):
        return sum(row.status in states for row in rows)

    pending = count((ActionStatus.staged, ActionStatus.approving, ActionStatus.executing))
    done = len(rows) - pending - count((ActionStatus.withdrawn,))
    ok = count((ActionStatus.succeeded, ActionStatus.reconciled))
    assert stats.pop("asOf") == local_now().strftime("%Y-%m-%d %H:%M")
    assert stats == {
        "total": len(rows),
        "ok": ok,
        "failed": count((ActionStatus.failed,)),
        "unknown": count((ActionStatus.unknown,)),
        "pending": pending,
        "confirm": count((ActionStatus.staged,)),
        "rate": ok / done if done else 0,
        "avgMs": int(sum(row.elapsed_ms or 0 for row in rows) / len(rows) + 0.5) if rows else 0,
    }
    assert (await client.get(API + "/action-types/missing/stats")).status_code == 404
    assert rows
    rows[0].submitted_at = now() - timedelta(days=31)
    await dyn.flush()
    assert (await client.get(API + f"/action-types/{code}/stats")).json()["total"] == len(rows) - 1
    rows[0].submitted_at = now() + timedelta(seconds=1)
    await dyn.flush()
    assert (await client.get(API + f"/action-types/{code}/stats")).json()["total"] == len(rows) - 1


async def test_historical_pages_never_load_current_child_definitions(dyn, client, engine):  # noqa: F811
    statements = []

    def captured(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", captured)
    try:
        for path in (
            "/action-types/request_replenish/params",
            "/event-types/order_status_changed/payload-fields",
        ):
            statements.clear()
            response = await client.get(API + path, params={"ver": "v2026.10.1", "size": 1})
            assert response.status_code == 200, response.text
            for table in (
                "action_param",
                "submission_criterion",
                "ontology_edit_rule",
                "side_effect",
                "event_payload_field",
                "event_subscription",
            ):
                assert not any(f"FROM {table} " in sql or f"FROM {table}\n" in sql for sql in statements)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", captured)
