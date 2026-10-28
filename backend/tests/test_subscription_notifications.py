"""Real PG source/recipient/rollback checks; no owner cache is delivered or provider called."""

import pytest
from sqlalchemy import delete, func, select

from app.common.auth import resolve_user
from app.common.outbox import emit
from app.common.pagination import PageParams
from app.core import clock
from app.modules.admin.models import SysRole, SysUser
from app.modules.notifications import service
from app.modules.notifications.models import Notification
from app.modules.qa.models import (
    PageSourceKind,
    QaPage,
    QaPageShareMember,
    QaPageSubscription,
    ShareMode,
    SharePerm,
    SubChannel,
    SubWhen,
)
from tests.use_ops_support import grant_ops_roles


@pytest.fixture
async def subscription(session):
    await grant_ops_roles(session)
    role = await session.scalar(select(SysRole).where(SysRole.code == "R-02"))
    role.func = {**role.func, "qa-pages": "view"}
    owner = await session.scalar(select(SysUser).where(SysUser.code == "U010001"))
    viewer = await session.scalar(select(SysUser).where(SysUser.code == "U010002"))
    page = QaPage(
        code="FIX-PAGE",
        owner_id=owner.id,
        name="订阅边界页面",
        source_kind=PageSourceKind.manual,
        refresh={"freq": "手动"},
        share_mode=ShareMode.members,
    )
    session.add(page)
    await session.flush()
    session.add(QaPageShareMember(page_id=page.id, user_id=viewer.id, perm=SharePerm.view))
    sub = QaPageSubscription(
        page_id=page.id,
        enabled=True,
        channel=SubChannel.inbox,
        recipients=[owner.name, viewer.name],
        send_when=SubWhen.after_refresh,
    )
    session.add(sub)
    await session.flush()
    event = await emit(
        session,
        "qa.page.subscription",
        {
            "ontologyId": 1,
            "pageId": page.code,
            "channel": "inbox",
            "to": [owner.code, viewer.code],
            "text": "SECRET OWNER CACHE 999999",
        },
        event_id="fix:page:subscription",
    )
    return page, sub, event, owner, viewer


async def test_source_backed_idempotent_and_no_cached_digest(session, subscription):
    page, _, event, owner, viewer = subscription
    assert await service.dispatch_workspace(session) == 1
    assert await service.dispatch_workspace(session) == 0
    rows = list(await session.scalars(select(Notification)))
    assert len(rows) == 2
    assert all(
        row.source_outbox_id == event.id
        and row.source_event_id is None
        and row.cr_id is None
        and row.source_kind == "page"
        for row in rows
    )
    for account in (owner, viewer):
        result = await service.inbox(session, await resolve_user(session, account.code), PageParams())
        assert result["total"] == result["unread"] == 1
        assert result["items"][0]["objId"] == page.code
        assert "999999" not in str(result) and "SECRET" not in str(result)


async def test_revocation_before_and_after_delivery_hides_count_and_markers(session, subscription):
    page, _, _, _, viewer = subscription
    await service.dispatch_workspace(session)
    await session.execute(delete(QaPageShareMember).where(QaPageShareMember.page_id == page.id))
    await session.flush()
    user = await resolve_user(session, viewer.code)
    result = await service.inbox(session, user, PageParams())
    assert result["items"] == [] and result["total"] == result["unread"] == 0
    await service.mark_read(session, user)
    row = await session.scalar(select(Notification).where(Notification.recipient_id == viewer.id))
    assert row.read_at is None


async def test_removed_recipient_not_delivered_and_other_workspace_not_consumed(session, subscription):
    page, _, _, owner, _ = subscription
    await session.execute(delete(QaPageShareMember).where(QaPageShareMember.page_id == page.id))
    foreign = await emit(
        session,
        "qa.page.subscription",
        {"ontologyId": 2, "pageId": page.code, "to": [owner.code], "channel": "inbox"},
        event_id="fix:other-workspace",
    )
    await service.dispatch_workspace(session)
    assert await session.scalar(select(func.count()).select_from(Notification)) == 1
    assert foreign.dispatched_at is None


async def test_disabled_subscription_no_notification_and_external_channel_retained(session, subscription):
    _, sub, event, _, _ = subscription
    event.payload = {**event.payload, "channel": "email"}
    assert await service.dispatch_workspace(session) == 1
    assert event.dispatched_at is None and event.attempts == 1
    assert event.last_error == "NOTIFICATION_CHANNEL_UNAVAILABLE" and event.available_at > clock.now()
    assert await session.scalar(select(func.count()).select_from(Notification)) == 0
    event.payload = {**event.payload, "channel": "inbox"}
    event.available_at = clock.now()
    sub.enabled = False
    await session.flush()
    assert await service.dispatch_workspace(session) == 1
    assert event.dispatched_at is not None
    assert await session.scalar(select(func.count()).select_from(Notification)) == 0


async def test_dispatch_failure_rolls_back_partial_notifications_then_retries(
    session, subscription, monkeypatch
):
    _, _, event, owner, _ = subscription
    real = service.page_subscription

    async def interrupted(session, event):
        session.add(
            Notification(
                source_kind="page",
                source_outbox_id=event.id,
                target_code="FIX-PAGE",
                recipient_id=owner.id,
                title="partial",
                description="partial",
            )
        )
        await session.flush()
        raise RuntimeError("PRIVATE SECRET SHOULD NOT BE STORED")

    monkeypatch.setattr(service, "page_subscription", interrupted)
    await service.dispatch_workspace(session)
    assert event.dispatched_at is None and event.last_error == "NOTIFICATION_DISPATCH_FAILED"
    assert await session.scalar(select(func.count()).select_from(Notification)) == 0
    monkeypatch.setattr(service, "page_subscription", real)
    event.available_at = clock.now()
    await session.flush()
    await service.dispatch_workspace(session)
    assert event.dispatched_at is not None and event.attempts == 2
    assert await session.scalar(select(func.count()).select_from(Notification)) == 2


async def test_cr_source_remains_compatible(session, subscription):
    from app.modules.static.models import ChangeRequest, CrEvent, CRLevel, CRStatus

    _, _, _, owner, _ = subscription
    cr = ChangeRequest(
        code="FIX-CR",
        title="原有审核通知",
        owner=owner.name,
        reviewer=owner.name,
        status=CRStatus.in_review,
        level=CRLevel.minor,
        status_at=clock.now(),
    )
    session.add(cr)
    await session.flush()
    event = CrEvent(cr_id=cr.id, action="submit", actor=owner.name, text="提交", at=clock.now())
    session.add(event)
    await session.flush()
    session.add(
        Notification(
            source_event_id=event.id,
            cr_id=cr.id,
            recipient_id=owner.id,
            title="原有待审核",
            description="审核通知",
        )
    )
    await session.flush()
    result = await service.inbox(session, await resolve_user(session, owner.code), PageParams())
    assert result["items"][0]["kind"] == "cr" and result["items"][0]["objId"] == cr.code


async def test_periodic_dispatch_accepts_worker_timestamp(monkeypatch):
    from unittest.mock import AsyncMock

    from app.jobs import dispatch_outbox

    invoke = AsyncMock(return_value=2)
    monkeypatch.setattr(service, "dispatch_subscriptions", invoke)
    assert await dispatch_outbox(timestamp=123456) == 2
    invoke.assert_awaited_once_with()


async def test_standard_event_worker_registered_task_reaches_real_inbox(session, subscription, monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from app.jobs import scope
    from app.modules.event.worker import QUEUE, build_app

    class ScopedTestSession:
        def __getattr__(self, name):
            return getattr(session, name)

        def begin(self):
            return session.begin_nested()

    @asynccontextmanager
    async def current():
        yield ScopedTestSession()

    monkeypatch.setattr(scope, "workspace_ids", AsyncMock(return_value=[1]))
    monkeypatch.setattr(scope, "scoped_sessions", lambda oid: current)
    worker = build_app()
    task = worker.tasks["outbox.dispatch"]
    assert task.queue == QUEUE
    assert ("outbox.dispatch", "outbox.dispatch") in worker.periodic_registry.periodic_tasks
    assert await task(timestamp=123456) == 1
    assert await session.scalar(select(func.count()).select_from(Notification)) == 2
