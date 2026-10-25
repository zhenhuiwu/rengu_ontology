"""事件类型只读接口（action-event-api §3.2），挂在 /api/ontology/v1。功能权限：event-types · view。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.auth import require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE
from app.db.session import SessionDep
from app.modules.action.schemas import ItemsOut, TypeVersionOut
from app.modules.action.service import paginate
from app.modules.event.schemas import (
    EventTypeDetailOut,
    EventTypeListOut,
    EventTypeOut,
    PayloadFieldOut,
    PayloadFieldPageOut,
    SubscriberOut,
)
from app.modules.event.service import EventTypeService

router = APIRouter(tags=["event"], dependencies=[Depends(require_func("event-types", "view"))])

PageNo = Annotated[int, Query(ge=1)]


@router.get(
    "/event-types", response_model=EventTypeListOut, summary="事件类型列表（来源 / 关键字筛选，分页）"
)
async def list_event_types(
    session: SessionDep,
    ctx: ViewContextDep,
    source: Annotated[str | None, Query(description="来源（中文）；全部 / 不传 = 不过滤")] = None,
    kw: Annotated[str | None, Query(description="名称包含")] = None,
    page: PageNo = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 22,
) -> EventTypeListOut:
    svc = EventTypeService(session)
    rows = await svc.search(ctx, source, kw)
    return EventTypeListOut(
        items=[EventTypeOut.model_validate(r) for r in paginate(rows, page, size)],
        sources=svc.sources(),
        total=len(rows),
        page=page,
        size=size,
    )


@router.get("/event-types/{type_id}", response_model=EventTypeDetailOut, summary="事件类型详情")
async def get_event_type(type_id: str, session: SessionDep, ctx: ViewContextDep) -> EventTypeDetailOut:
    return EventTypeDetailOut.model_validate(await EventTypeService(session).detail(ctx, type_id))


@router.get(
    "/event-types/{type_id}/payload-fields",
    response_model=PayloadFieldPageOut,
    summary="载荷字段（专有字段 + 通用信封字段，分页）",
)
async def list_payload_fields(
    type_id: str,
    session: SessionDep,
    ctx: ViewContextDep,
    page: PageNo = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 12,
) -> PayloadFieldPageOut:
    rows, env_count = await EventTypeService(session).payload_fields(ctx, type_id)
    return PayloadFieldPageOut(
        items=[PayloadFieldOut.model_validate(r) for r in paginate(rows, page, size)],
        envelope_count=env_count,
        total=len(rows),
        page=page,
        size=size,
    )


@router.get("/event-types/{type_id}/subscribers", response_model=ItemsOut[SubscriberOut], summary="订阅方")
async def list_subscribers(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ItemsOut[SubscriberOut]:
    rows = await EventTypeService(session).subscribers(ctx, type_id)
    return ItemsOut[SubscriberOut](items=[SubscriberOut.model_validate(r) for r in rows])


@router.get("/event-types/{type_id}/versions", response_model=ItemsOut[TypeVersionOut], summary="版本记录")
async def list_versions(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ItemsOut[TypeVersionOut]:
    rows = await EventTypeService(session).versions(ctx, type_id)
    return ItemsOut[TypeVersionOut](items=[TypeVersionOut.model_validate(r) for r in rows])
