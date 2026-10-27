"""One real source and one recipient boundary shared by list/count/read operations."""

from sqlalchemy import func, select, update

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
    recipient_id = await session.scalar(
        select(SysUser.id)
        .join(OntologyMember, OntologyMember.user_id == SysUser.id)
        .where(
            SysUser.code == user.id,
            SysUser.status == UserStatus.active,
            OntologyMember.ontology_id == ontology_id,
            OntologyMember.role.in_(("owner", "editor", "viewer")),
        )
    )
    if recipient_id is None:
        raise Forbidden("没有当前本体的站内通知访问权限")
    return Notification.ontology_id == ontology_id, Notification.recipient_id == recipient_id


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
        .join(ChangeRequest, ChangeRequest.id == Notification.cr_id)
        .where(*selected)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .offset(p.offset)
        .limit(p.size)
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "kind": "cr",
                "objId": code,
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
