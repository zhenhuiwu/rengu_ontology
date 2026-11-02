"""Offline aggregation contract check; no database, HTTP, workers or execution."""

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from app.modules.action import service
from app.modules.dynamic.models import ActionStatus


async def main():
    today = date(2026, 11, 1)
    now = datetime(2026, 11, 1, 8, tzinfo=UTC)
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=16),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(ActionStatus.succeeded, 1, 12)])),
    )
    with patch.object(service.clock, "now", return_value=now):
        stats = await service.ActionTypeService(session).stats("recordInspection")
    assert stats["total"] == stats["ok"] == 1 and stats["rate"] == 1
    query = session.execute.call_args.args[0]
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "in_center" not in sql and "recordInspection" in sql
    assert "submitted_at >=" in sql and "submitted_at <=" in sql

    session.execute.return_value = SimpleNamespace(all=lambda: [
        (today, ActionStatus.succeeded, 1), (today, ActionStatus.reconciled, 2),
        (today, ActionStatus.failed, 3), (today, ActionStatus.unknown, 4),
        (today, ActionStatus.staged, 5), (today, ActionStatus.withdrawn, 6),
    ])
    repo = SimpleNamespace(get_by_code_or_404=AsyncMock(return_value=SimpleNamespace(code="recordInspection")))
    with (
        patch.object(service, "ActionTypeRepo", return_value=repo),
        patch.object(service.clock, "now", return_value=now),
        patch.object(service.clock, "last_days", return_value=(date(2026, 10, 31), today)),
        patch.object(service, "get_settings", return_value=SimpleNamespace(display_tz="Asia/Shanghai")),
    ):
        daily = await service.run_daily(session, "recordInspection", 2)
    assert daily == [
        {"date": "10-31", "ok": 0, "failed": 0, "unknown": 0},
        {"date": "11-01", "ok": 3, "failed": 3, "unknown": 4},
    ]
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "in_center" not in sql and "action_run_daily_stat" not in sql
    assert "recordInspection" in sql and "Asia/Shanghai" in sql
    assert "2026-10-31" in sql and "2026-11-01" in sql and "submitted_at <=" in sql
    print("Action runtime stats and daily contracts passed (offline).")


if __name__ == "__main__":
    asyncio.run(main())
