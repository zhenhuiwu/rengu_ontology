"""Opt-in pure scheduling/permission self-check. No database, worker, source or HTTP calls."""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.errors import AppError, Forbidden
from app.modules.data import quality_scheduler as scheduler


async def main():
    at = datetime.fromisoformat("2026-10-27T23:30:00+08:00")
    with patch.object(scheduler, "get_settings", return_value=SimpleNamespace(display_tz="Asia/Shanghai")):
        assert scheduler.slot("每天 23:30", at).isoformat() == "2026-10-28T15:30:00+00:00"
        assert scheduler.slot("每天 23:30", at, previous=True).isoformat() == "2026-10-27T15:30:00+00:00"
        assert scheduler.slot("每 2 小时", at).isoformat() == "2026-10-27T16:00:00+00:00"
        assert scheduler.slot("每周一 06:00", at).isoformat() == "2026-11-01T22:00:00+00:00"
        try:
            scheduler.slot("手动", at)
        except AppError as error:
            assert error.code == "QUALITY_SCHEDULE_INVALID"
        else:
            raise AssertionError("Manual definitions cannot create timers")
    user = SimpleNamespace(
        ontology_id=1,
        ontology_role="owner",
        id="test-only-user",
        func={"data-quality": "manage"},
        roles=(),
        row_filters={},
        attrs={},
    )
    initial = scheduler.fingerprint(user)
    user.ontology_id = 2
    assert scheduler.fingerprint(user) != initial
    for role, scope in [("viewer", None), ("editor", {"denyAll": True})]:
        user.ontology_role = role
        # Use the same DENY_ALL value as production permission aggregation.
        from app.common.row_filter import DENY_ALL

        with (
            patch.object(scheduler, "resolve_user", AsyncMock(return_value=user)),
            patch.object(
                scheduler, "row_filter", AsyncMock(return_value=DENY_ALL if scope is not None else None)
            ),
        ):
            try:
                await scheduler.actor(None, user.id, "test-only-object")
            except Forbidden:
                pass
            else:
                raise AssertionError("Viewer or denied object permissions authorized a schedule")


if __name__ == "__main__":
    asyncio.run(main())
