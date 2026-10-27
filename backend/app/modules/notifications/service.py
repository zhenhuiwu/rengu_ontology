"""One real source and one recipient boundary shared by list/count/read operations."""

from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update

from app.common.auth import check_func, resolve_user
from app.common.ontology import require_ontology_id
from app.common.pagination import PageParams
from app.core import clock
from app.core.errors import AppError, Forbidden, NotFound
from app.core.schema import _fmt_dt
from app.modules.admin.models import SysUser, UserStatus
from app.modules.notifications.models import Notification
from app.modules.ontology.models import OntologyMember
from app.modules.static.models import ChangeRequest, CRStatus


async def own_scope(session, user):
    ontology_id = require_ontology_id(session)
    if user.ontology_id != ontology_id or user.ontology_ids is not None or user.is_service_account:
        raise Forbidden("站内通知仅供当前本体的本人身份访问")
    account = await session.scalar(
        select(SysUser)
        .join(OntologyMember, OntologyMember.user_id == SysUser.id)
        .where(
            SysUser.code == user.id,
            SysUser.status == UserStatus.active,
            OntologyMember.ontology_id == ontology_id,
            OntologyMember.role.in_(("owner", "editor", "viewer")),
        )
    )
    if account is None:
        raise Forbidden("没有当前本体的站内通知访问权限")
    from app.modules.qa.models import QaPage
    from app.modules.qa.repo import PageRepo

    visible = select(QaPage.code).where(
        PageRepo(session).visible_clause(account.id, account.dept, include_shared=True)
    )
    page_allowed = user.func.get("qa-pages") in ("view", "manage")
    return (
        Notification.ontology_id == ontology_id,
        Notification.recipient_id == account.id,
        or_(
            Notification.source_kind == "cr",
            and_(Notification.source_kind == "page", Notification.target_code.in_(visible), page_allowed),
        ),
    )


async def count_unread(session, scope):
    return int(
        await session.scalar(
            select(func.count()).select_from(Notification).where(*scope, Notification.read_at.is_(None))
        )
        or 0
    )


async def inbox(session, user, page: PageParams, unread_only: bool = False):
    scope = await own_scope(session, user)
    selected = (*scope, Notification.read_at.is_(None)) if unread_only else scope
    total = int(await session.scalar(select(func.count()).select_from(Notification).where(*selected)) or 0)
    p = page.clamp(total)
    rows = await session.execute(
        select(Notification, ChangeRequest.code)
        .outerjoin(ChangeRequest, ChangeRequest.id == Notification.cr_id)
        .where(*selected)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(p.offset)
        .limit(p.size)
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "kind": row.source_kind,
                "objId": row.target_code if row.source_kind == "page" else code,
                "title": row.title,
                "desc": row.description,
                "time": _fmt_dt(row.created_at),
                "read": row.read_at is not None,
            }
            for row, code in rows
        ],
        "total": total,
        "page": p.page,
        "size": p.size,
        "unread": await count_unread(session, scope),
    }


async def mark_read(session, user, notification_id: int | None = None):
    scope = await own_scope(session, user)
    if notification_id is None:
        await session.execute(
            update(Notification).where(*scope, Notification.read_at.is_(None)).values(read_at=clock.now())
        )
    else:
        row = await session.scalar(
            select(Notification).where(*scope, Notification.id == notification_id).with_for_update()
        )
        if row is None:
            raise NotFound("通知不存在")
        if row.read_at is None:
            row.read_at = clock.now()
            await session.flush()
    return {"unread": await count_unread(session, scope)}


async def review_submitted(session, cr, event, submitter):
    """Called after the source event flush, within the same CR submission transaction."""
    from app.modules.static.cr_write import CrWriteService
    from app.modules.static.permissions import check_cr

    ontology_id = require_ontology_id(session)
    if (
        cr.ontology_id != ontology_id
        or event.ontology_id != ontology_id
        or event.cr_id != cr.id
        or event.action != "submit"
        or cr.status != CRStatus.in_review
    ):
        raise AppError("通知来源与提交记录不一致", code="NOTIFICATION_SOURCE_INVALID", status_code=409)
    candidates = await CrWriteService(session).reviewer_options(cr.code, submitter)
    reviewer_code = (cr.detail or {}).get("reviewerCode")
    matches = (
        [item for item in candidates if item["id"] == reviewer_code]
        if reviewer_code
        else [item for item in candidates if item["name"] == cr.reviewer]
    )
    if len(matches) != 1:
        raise AppError("审核人不存在或不唯一，请重新指定", code="CR_REVIEWER_INVALID", status_code=409)
    reviewer = await resolve_user(session, matches[0]["id"])
    if reviewer.ontology_role not in ("owner", "editor"):
        raise Forbidden("审核人没有当前本体的编辑权限，请重新指定")
    check_func(reviewer, "releases", "view")
    await check_cr(session, cr, reviewer)
    scope = await own_scope(session, reviewer)
    recipient_id = await session.scalar(select(SysUser.id).where(SysUser.code == reviewer.id))
    if await session.scalar(select(Notification.id).where(*scope, Notification.source_event_id == event.id)):
        return
    session.add(
        Notification(
            recipient_id=recipient_id,
            source_event_id=event.id,
            cr_id=cr.id,
            title=f"{cr.code} 待审核",
            description=f"{submitter.name}提交了「{cr.title}」",
            created_by=submitter.id,
            created_at=event.at,
        )
    )
    await session.flush()


async def page_subscription(session, event):
    """Notify only current page recipients, without forwarding the owner's cached digest."""
    from app.modules.qa.models import QaPage, QaPageSubscription, SubChannel
    from app.modules.qa.page_schedule import _recipients
    from app.modules.qa.repo import PageRepo

    ontology_id = require_ontology_id(session)
    payload = event.payload or {}
    if (
        payload.get("ontologyId") != ontology_id
        or not isinstance(payload.get("pageId"), str)
        or not isinstance(payload.get("to"), list)
        or any(not isinstance(code, str) for code in payload["to"])
    ):
        raise AppError("页面订阅来源无效", code="NOTIFICATION_SOURCE_INVALID", status_code=422)
    if payload.get("channel") != SubChannel.inbox.name:
        raise AppError("外部通知渠道尚未配置", code="NOTIFICATION_CHANNEL_UNAVAILABLE", status_code=503)
    page = await session.scalar(select(QaPage).where(QaPage.code == payload["pageId"]))
    if page is None:
        return
    sub = await session.scalar(select(QaPageSubscription).where(QaPageSubscription.page_id == page.id))
    if sub is None or not sub.enabled or sub.channel != SubChannel.inbox:
        return
    current, _ = await _recipients(session, page, sub)
    for code in sorted(set(payload["to"]) & set(current)):
        try:
            user = await resolve_user(session, code)
            check_func(user, "qa-pages", "view")
            scope = await own_scope(session, user)
        except AppError as error:
            if error.status_code not in (401, 403):
                raise
            continue
        account = await session.scalar(select(SysUser).where(SysUser.code == code))
        if not await PageRepo(session).is_visible(
            page.id, PageRepo(session).visible_clause(account.id, account.dept, include_shared=True)
        ):
            continue
        if await session.scalar(
            select(Notification.id).where(*scope, Notification.source_outbox_id == event.id)
        ):
            continue
        session.add(
            Notification(
                source_kind="page",
                source_outbox_id=event.id,
                target_code=page.code,
                recipient_id=account.id,
                title=f"页面订阅：{page.name}"[:200],
                description="订阅页面已更新，请打开页面查看当前权限下的结果。",
                created_at=event.created_at,
                created_by="system:page-subscription",
            )
        )
    await session.flush()


async def dispatch_workspace(session, *, limit: int = 100):
    from app.common.outbox import OutboxEvent

    ontology_id = require_ontology_id(session)
    rows = list(
        await session.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.topic == "qa.page.subscription",
                OutboxEvent.payload["ontologyId"].as_string() == str(ontology_id),
                OutboxEvent.dispatched_at.is_(None),
                OutboxEvent.available_at <= clock.now(),
            )
            .order_by(OutboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    for event in rows:
        try:
            async with session.begin_nested():
                await page_subscription(session, event)
        except Exception as error:
            # Never persist exception text: drivers/payloads can contain private data or credentials.
            event.attempts += 1
            event.last_error = error.code if isinstance(error, AppError) else "NOTIFICATION_DISPATCH_FAILED"
            event.available_at = clock.now() + timedelta(seconds=min(3600, 30 * 2 ** min(event.attempts, 7)))
        else:
            event.attempts += 1
            event.last_error = None
            event.dispatched_at = clock.now()
    await session.flush()
    return len(rows)


async def dispatch_subscriptions():
    from app.jobs.scope import scoped_sessions, workspace_ids

    count = 0
    for ontology_id in await workspace_ids():
        async with scoped_sessions(ontology_id)() as session, session.begin():
            count += await dispatch_workspace(session)
    return count
