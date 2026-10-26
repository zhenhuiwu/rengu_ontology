"""事件类型只读接口（action-event-api §3.2、§12），挂在 /api/ontology/v1。功能权限：event-types · view。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.common.auth import require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.db.session import SessionDep
from app.modules.action.schemas import ItemsOut, TypeVersionOut
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

router = APIRouter(tags=["event"], dependencies=[Depends(require_func("event-types", "view"))])


def _list_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 22,
) -> PageParams:
    """前端事件列表默认 size=22；page ≤ 0 收成 1（不报 422）。"""
    return PageParams(page=page, size=size)


def _nested_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 12,
) -> PageParams:
    """前端载荷字段默认 size=12。"""
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
    rows = await svc.search(ctx, source, kw)
    p = page.clamp(len(rows))
    return EventTypeListOut(
        items=[EventTypeOut.model_validate(r) for r in rows[p.offset : p.offset + p.size]],
        sources=svc.sources(),
        total=len(rows),
        page=p.page,
        size=p.size,
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
    page: Annotated[PageParams, Depends(_nested_page)],
) -> PayloadFieldPageOut:
    rows, env_count = await EventTypeService(session).payload_fields(ctx, type_id)
    p = page.clamp(len(rows))
    return PayloadFieldPageOut(
        items=[PayloadFieldOut.model_validate(r) for r in rows[p.offset : p.offset + p.size]],
        envelope_count=env_count,
        total=len(rows),
        page=p.page,
        size=p.size,
    )


@router.get("/event-types/{type_id}/subscribers", response_model=ItemsOut[SubscriberOut], summary="订阅方")
async def list_subscribers(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ItemsOut[SubscriberOut]:
    rows = await EventTypeService(session).subscribers(ctx, type_id)
    return ItemsOut[SubscriberOut](items=[SubscriberOut.model_validate(r) for r in rows])


@router.get("/event-types/{type_id}/versions", response_model=ItemsOut[TypeVersionOut], summary="版本记录")
async def list_versions(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ItemsOut[TypeVersionOut]:
    rows = await EventTypeService(session).versions(ctx, type_id)
    return ItemsOut[TypeVersionOut](items=[TypeVersionOut.model_validate(r) for r in rows])


# ---- 运行数据（palantir-r2，action-event-api §12） ----

OutcomeQ = Literal["全部", "已投递", "已生成告警", "已去重", "部分投递", "投递失败"]


def _event_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 14,
) -> PageParams:
    """前端近期事件默认 size=14（usePagedQuery defaultSize）。"""
    return PageParams(page=page, size=size)


@router.get(
    "/event-types/{type_id}/events",
    response_model=EventPageOut,
    summary="近期事件（时间范围 / 结果筛选，按时间倒序分页）",
)
async def list_events(
    type_id: str,
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
async def get_event_stats(type_id: str, session: SessionDep) -> EventStatsOut:
    return EventStatsOut.model_validate(await EventRuntimeService(session).stats(type_id))
