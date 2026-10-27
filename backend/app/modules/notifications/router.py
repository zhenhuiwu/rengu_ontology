from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select

from app.common.auth import CurrentUserDep, require_func
from app.common.context import readonly_extra
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.db.session import SessionDep
from app.modules.notifications import service
from app.modules.notifications.models import Notification

router = APIRouter(tags=["notifications"])
settings_router = APIRouter(tags=["admin"], dependencies=[Depends(require_func("settings", "view"))])


@router.get("/notifications")
async def inbox(
    session: SessionDep,
    user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
    unread_only: Annotated[bool, Query(alias="unreadOnly")] = False,
):
    return await service.inbox(session, user, PageParams(page=page, size=size), unread_only)


@router.get("/notifications/unread")
async def unread(session: SessionDep, user: CurrentUserDep):
    return {"unread": await service.count_unread(session, await service.own_scope(session, user))}


@router.post("/notifications:read-all", openapi_extra=readonly_extra("history", "preview"))
async def read_all(session: SessionDep, user: CurrentUserDep):
    return await service.mark_read(session, user)


@router.post("/notifications/{notification_id}:read", openapi_extra=readonly_extra("history", "preview"))
async def read_one(notification_id: Annotated[int, Path(ge=1)], session: SessionDep, user: CurrentUserDep):
    return await service.mark_read(session, user, notification_id)


@settings_router.get("/settings/notifications")
async def channel_status(session: SessionDep):
    # Readiness is backed by the deployed table; no user inbox contents leave this settings endpoint.
    await session.execute(select(Notification.id).limit(0))
    return {"inbox": {"enabled": True, "sources": ["cr-review"]}, "externalChannelsConfigured": False}
