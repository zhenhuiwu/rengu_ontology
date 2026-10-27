"""事件类型只读接口（action-event-api §3.2、§12），挂在 /api/ontology/v1。功能权限：event-types · view。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Path, Query

from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.db.session import SessionDep
from app.modules.action.schemas import ItemsOut, TypeVersionOut
from app.modules.event.exceptions import DispositionIn
from app.modules.event.schemas import (
    EventOut,
    EventPageOut,
    EventStatsOut,
    EventTypeDetailOut,
    EventTypeListOut,
    EventTypeOut,
    PayloadFieldOut,
    PayloadFieldPageOut,
    SubscriberOut,
)
from app.modules.event.service import EventRuntimeService, EventTypeService
from app.modules.event.simulation import SimulateIn, SimulateOut, simulate

router = APIRouter(tags=["event"], dependencies=[Depends(require_func("event-types", "view"))])
EventPathId = Annotated[str, Path(pattern=r"^[^\x00]+$")]


@router.get("/event-types/{type_id}/delivery-exceptions", summary="按真实对象权限筛选的投递异常")
async def delivery_exceptions(
    type_id: EventPathId,
    session: SessionDep,
    user: CurrentUserDep,
    state: Annotated[Literal["unknown", "failed", "quarantined", "pending"] | None, Query()] = None,
    kw: Annotated[str | None, Query(max_length=128)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> dict:
    from app.modules.event.exceptions import inbox

    return await inbox(session, user, type_id, state, kw, page, size)


@router.get("/event-deliveries/{delivery_id}", summary="投递及不可变尝试、人工处置记录")
async def delivery_detail(
    delivery_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
    user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> dict:
    from app.modules.event.exceptions import detail

    return await detail(session, user, delivery_id, page, size)


@router.post("/event-deliveries/{delivery_id}:dispose", summary="显式证据与授权的投递人工处置")
async def dispose_delivery(
    delivery_id: Annotated[int, Path(ge=1)],
    body: DispositionIn,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
) -> dict:
    from app.modules.event.exceptions import dispose

    return await dispose(session, user, ctx, delivery_id, body)


@router.post(
    "/event-types/{type_id}:simulate",
    response_model=SimulateOut,
    summary="校验载荷与过滤；仅对显式配置测试通道排队测试事件",
)
async def simulate_event(
    type_id: EventPathId,
    session: SessionDep,
    user: CurrentUserDep,
    view: Annotated[Literal["published", "draft"], Query()] = "published",
    cr: Annotated[str | None, Query(max_length=64)] = None,
    body: Annotated[SimulateIn | None, Body()] = None,
) -> SimulateOut:
    return await simulate(session, type_id, view, cr, body or SimulateIn(), user)


@router.get("/events/{event_id}/deliveries", summary="真实订阅投递状态（不包含路由凭据）")
async def event_deliveries(
    event_id: EventPathId,
    session: SessionDep,
    user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> dict:
    from sqlalchemy import func, select

    from app.core.errors import NotFound
    from app.modules.event.exceptions import authorize, output
    from app.modules.event.models import Event, EventDelivery

    event = await session.scalar(select(Event).where(Event.code == event_id))
    if event is None:
        raise NotFound("事件不存在")
    await authorize(session, user, event)
    query = select(EventDelivery).where(EventDelivery.event_id == event.id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    page = min(page, max(1, (total + size - 1) // size))
    rows = await session.scalars(query.order_by(EventDelivery.id).offset((page - 1) * size).limit(size))
    return {
        "eventId": event.code,
        "outcome": event.outcome.value,
        "summary": event.summary,
        "total": total,
        "page": page,
        "size": size,
        "items": [output(row, event) for row in rows],
    }


def _list_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> PageParams:
    """默认10条；page ≤ 0 收成 1。"""
    return PageParams(page=page, size=size)


def _nested_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> PageParams:
    """载荷字段默认10条。"""
    return PageParams(page=page, size=size)


@router.get(
    "/event-types", response_model=EventTypeListOut, summary="事件类型列表（来源 / 关键字筛选，分页）"
)
async def list_event_types(
    session: SessionDep,
    ctx: ViewContextDep,
    page: Annotated[PageParams, Depends(_list_page)],
    source: Annotated[str | None, Query(description="来源（中文）；全部 / 不传 = 不过滤")] = None,
    kw: Annotated[str | None, Query(description="名称包含")] = None,
) -> EventTypeListOut:
    svc = EventTypeService(session)
    result = await svc.search_page(ctx, page, source, kw)
    return EventTypeListOut(
        items=[EventTypeOut.model_validate(r) for r in result.items],
        sources=svc.sources(),
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/event-types/{type_id}", response_model=EventTypeDetailOut, summary="事件类型详情")
async def get_event_type(
    type_id: EventPathId, session: SessionDep, ctx: ViewContextDep
) -> EventTypeDetailOut:
    return EventTypeDetailOut.model_validate(await EventTypeService(session).detail(ctx, type_id))


@router.get(
    "/event-types/{type_id}/payload-fields",
    response_model=PayloadFieldPageOut,
    summary="载荷字段（专有字段 + 通用信封字段，分页）",
)
async def list_payload_fields(
    type_id: EventPathId,
    session: SessionDep,
    ctx: ViewContextDep,
    page: Annotated[PageParams, Depends(_nested_page)],
) -> PayloadFieldPageOut:
    result, env_count = await EventTypeService(session).payload_page(ctx, type_id, page)
    return PayloadFieldPageOut(
        items=[PayloadFieldOut.model_validate(r) for r in result.items],
        envelope_count=env_count,
        total=result.total,
        page=result.page,
        size=result.size,
    )


@router.get("/event-types/{type_id}/subscribers", response_model=ItemsOut[SubscriberOut], summary="订阅方")
async def list_subscribers(
    type_id: EventPathId, session: SessionDep, ctx: ViewContextDep
) -> ItemsOut[SubscriberOut]:
    rows = await EventTypeService(session).subscribers(ctx, type_id)
    return ItemsOut[SubscriberOut](items=[SubscriberOut.model_validate(r) for r in rows])


@router.get("/event-types/{type_id}/versions", response_model=ItemsOut[TypeVersionOut], summary="版本记录")
async def list_versions(
    type_id: EventPathId, session: SessionDep, ctx: ViewContextDep
) -> ItemsOut[TypeVersionOut]:
    rows = await EventTypeService(session).versions(ctx, type_id)
    return ItemsOut[TypeVersionOut](items=[TypeVersionOut.model_validate(r) for r in rows])


# ---- 运行数据（palantir-r2，action-event-api §12） ----

OutcomeQ = Literal["全部", "已投递", "已生成告警", "已去重", "部分投递", "投递失败", "待投递"]


def _event_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> PageParams:
    """近期事件默认10条。"""
    return PageParams(page=page, size=size)


@router.get(
    "/event-types/{type_id}/events",
    response_model=EventPageOut,
    summary="近期事件（时间范围 / 结果筛选，按时间倒序分页）",
)
async def list_events(
    type_id: EventPathId,
    session: SessionDep,
    page: Annotated[PageParams, Depends(_event_page)],
    range_: Annotated[Literal["24h", "7d"], Query(alias="range", description="24h / 7d（默认）")] = "7d",
    outcome: Annotated[OutcomeQ | None, Query(description="结果（中文）；全部 / 不传 = 不过滤")] = None,
) -> EventPageOut:
    rows, total, p = await EventRuntimeService(session).events(type_id, range_, outcome, page)
    return EventPageOut(
        items=[EventOut.model_validate(r) for r in rows], total=total, page=p.page, size=p.size
    )


@router.get("/event-types/{type_id}/stats", response_model=EventStatsOut, summary="运行健康 + 近 14 天事件数")
async def get_event_stats(type_id: EventPathId, session: SessionDep) -> EventStatsOut:
    return EventStatsOut.model_validate(await EventRuntimeService(session).stats(type_id))


@router.get("/events/{event_id}", response_model=EventOut, summary="单条事件（事件抽屉）")
async def get_event(event_id: EventPathId, session: SessionDep) -> EventOut:
    return EventOut.model_validate(await EventRuntimeService(session).event(event_id))
