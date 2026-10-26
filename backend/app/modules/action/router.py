"""动作类型只读接口（action-event-api §3.1），挂在 /api/ontology/v1。

全部支持 `?ver=`（读发布快照）和 `?previewAs=`（只读；行级过滤对定义类数据不适用）。
功能权限：action-types · view。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.auth import require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, Page, PageParams
from app.db.session import SessionDep
from app.modules.action.schemas import (
    ActionTypeDetailOut,
    ActionTypeListOut,
    ActionTypeOut,
    CriterionOut,
    ItemsOut,
    ParamOut,
    RunDailyOut,
    TypeVersionOut,
    WritebackOut,
)
from app.modules.action.service import ActionTypeService, run_daily

router = APIRouter(tags=["action"], dependencies=[Depends(require_func("action-types", "view"))])


def _list_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 22,
) -> PageParams:
    """前端动作列表默认 size=22；page ≤ 0 收成 1（不报 422）。"""
    return PageParams(page=page, size=size)


def _nested_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 12,
) -> PageParams:
    """前端参数默认 size=12。"""
    return PageParams(page=page, size=size)


@router.get(
    "/action-types", response_model=ActionTypeListOut, summary="动作类型列表（分组 / 关键字筛选，分页）"
)
async def list_action_types(
    session: SessionDep,
    ctx: ViewContextDep,
    page: Annotated[PageParams, Depends(_list_page)],
    group: Annotated[str | None, Query(description="分组（中文）；全部 / 不传 = 不过滤")] = None,
    kw: Annotated[str | None, Query(description="名称包含")] = None,
) -> ActionTypeListOut:
    rows, groups = await ActionTypeService(session).search(ctx, group, kw)
    p = page.clamp(len(rows))
    return ActionTypeListOut(
        items=[ActionTypeOut.model_validate(r) for r in rows[p.offset : p.offset + p.size]],
        groups=groups,
        total=len(rows),
        page=p.page,
        size=p.size,
    )


@router.get("/action-types/{type_id}", response_model=ActionTypeDetailOut, summary="动作类型详情（概览）")
async def get_action_type(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ActionTypeDetailOut:
    return ActionTypeDetailOut.model_validate(await ActionTypeService(session).detail(ctx, type_id))


@router.get("/action-types/{type_id}/params", response_model=Page[ParamOut], summary="动作参数（分页）")
async def list_params(
    type_id: str,
    session: SessionDep,
    ctx: ViewContextDep,
    page: Annotated[PageParams, Depends(_nested_page)],
) -> Page[ParamOut]:
    rows = (await ActionTypeService(session).one(ctx, type_id)).get("params") or []
    p = page.clamp(len(rows))
    return Page.of([ParamOut.model_validate(r) for r in rows[p.offset : p.offset + p.size]], len(rows), page)


@router.get(
    "/action-types/{type_id}/submission-criteria", response_model=ItemsOut[CriterionOut], summary="提交条件"
)
async def list_criteria(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ItemsOut[CriterionOut]:
    rows = (await ActionTypeService(session).one(ctx, type_id)).get("conds") or []
    return ItemsOut[CriterionOut](items=[CriterionOut.model_validate(r) for r in rows])


@router.get(
    "/action-types/{type_id}/writeback", response_model=WritebackOut, summary="写回与副作用（含执行顺序）"
)
async def get_writeback(type_id: str, session: SessionDep, ctx: ViewContextDep) -> WritebackOut:
    return WritebackOut.model_validate(await ActionTypeService(session).writeback(ctx, type_id))


@router.get("/action-types/{type_id}/versions", response_model=ItemsOut[TypeVersionOut], summary="版本记录")
async def list_versions(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ItemsOut[TypeVersionOut]:
    rows = await ActionTypeService(session).versions(ctx, type_id)
    return ItemsOut[TypeVersionOut](items=[TypeVersionOut.model_validate(r) for r in rows])


@router.get(
    "/action-types/{type_id}/stats/daily",
    response_model=ItemsOut[RunDailyOut],
    summary="近 N 天每日执行量（成功 / 失败 / 结果未知）",
)
async def get_run_daily(
    type_id: str,
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=90, description="天数，默认 14（前端图表）")] = 14,
) -> ItemsOut[RunDailyOut]:
    rows = await run_daily(session, type_id, days)
    return ItemsOut[RunDailyOut](items=[RunDailyOut.model_validate(r) for r in rows])
