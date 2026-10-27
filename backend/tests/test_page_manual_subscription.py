"""Manual refresh must use the same transactional inbox hook as scheduled refresh."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.qa import page_schedule, pages_api
from app.modules.qa.models import PageStatus, SubChannel, SubWhen


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["success", "failed", "absent", "disabled", "unchanged", "email"])
async def test_manual_refresh_subscription(monkeypatch, case):
    at = datetime(2026, 10, 28, tzinfo=timezone.utc)
    sub = SimpleNamespace(enabled=case != "disabled", channel=SubChannel.email if case == "email" else SubChannel.inbox,
                          send_when=SubWhen.after_refresh, only_changed=True, recipients=[])
    card = SimpleNamespace(result_cache={"value": 1}, perm_fingerprint="old")
    page = SimpleNamespace(code="WF-MANUAL", cards=[card], subscription=None if case == "absent" else sub,
                           status=PageStatus.ok, status_note=None, last_refresh=None, data_as_of=None)
    actor, session = object(), AsyncMock()
    monkeypatch.setattr(pages_api, "_load", AsyncMock(return_value=(1, page)))
    monkeypatch.setattr(pages_api, "_out", AsyncMock(return_value="result"))
    monkeypatch.setattr(pages_api, "_status_of", lambda cards: (PageStatus.ok, None))
    monkeypatch.setattr(pages_api.clock, "now", lambda: at)
    monkeypatch.setattr(pages_api.execution, "current_viewer", AsyncMock(return_value=actor))
    monkeypatch.setattr(pages_api.execution, "fingerprint", AsyncMock(return_value="current"))
    run = AsyncMock(return_value=([{"value": 1 if case == "unchanged" else 2}], at))
    if case == "failed":
        run.side_effect = AppError("不可查询", code="QA_PAGE_UNAVAILABLE", status_code=503)
    monkeypatch.setattr(pages_api.execution, "execute_cards", run)
    monkeypatch.setattr(pages_api, "audit", AsyncMock())
    monkeypatch.setattr(page_schedule, "audit", AsyncMock())
    payload, emit = AsyncMock(return_value={"kind": "afterRefresh"}), AsyncMock()
    monkeypatch.setattr(page_schedule, "_payload", payload)
    monkeypatch.setattr(page_schedule, "_emit_once", emit)

    assert await pages_api.refresh_page(page.code, session, actor, object()) == "result"
    if case == "success":
        emit.assert_awaited_once_with(session, f"qa.page.refresh:{page.code}:{at.isoformat()}", {"kind": "afterRefresh"})
        payload.assert_awaited_once_with(session, page, sub, "afterRefresh", at.isoformat(), True)
        assert card.result_cache == {"value": 2} and card.perm_fingerprint == "current"
    else:
        emit.assert_not_awaited()
        payload.assert_not_awaited()
    if case == "failed":
        assert page.status == PageStatus.refresh_failed and page.last_refresh is None
        assert card.result_cache == {"value": 1} and card.perm_fingerprint == "old"
    session.commit.assert_not_awaited()  # The request owns both cache and outbox transaction.


@pytest.mark.asyncio
async def test_subscription_write_error_is_not_reported_as_refresh_success(monkeypatch):
    sub = SimpleNamespace(enabled=True, channel=SubChannel.inbox, send_when=SubWhen.after_refresh,
                          only_changed=False, recipients=[])
    page = SimpleNamespace(code="WF-MANUAL", cards=[], subscription=sub)
    monkeypatch.setattr(page_schedule, "audit", AsyncMock())
    monkeypatch.setattr(page_schedule, "_payload", AsyncMock(return_value={"kind": "afterRefresh"}))
    monkeypatch.setattr(page_schedule, "_emit_once", AsyncMock(side_effect=RuntimeError("outbox unavailable")))
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        await page_schedule.emit_refresh_subscription(
            AsyncMock(), page, object(), datetime(2026, 10, 28, tzinfo=timezone.utc), before=[], ok=True,
        )
