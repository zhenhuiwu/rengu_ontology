"""动作类型只读接口（action-event-api §3.1），挂在 /api/ontology/v1。

全部支持 `?ver=`（读发布快照）和 `?previewAs=`（只读；行级过滤对定义类数据不适用）。
功能权限：action-types · view。
"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep, readonly_extra
from app.common.pagination import MAX_PAGE_SIZE, Page, PageParams
from app.db.session import SessionDep
from app.modules.action.checker import DatabaseSubmissionChecker, definition
from app.modules.action.schemas import (
    ActionTypeDetailOut,
    ActionTypeListOut,
    ActionTypeOut,
    CriterionOut,
    DryRunIn,
    DryRunOut,
    ItemsOut,
    ParamOut,
    RunDailyOut,
    TypeVersionOut,
    UsedByOut,
    WritebackOut,
)
from app.modules.action.service import ActionTypeService, run_daily

router = APIRouter(tags=["action"], dependencies=[Depends(require_func("action-types", "view"))])


@router.post(
    "/action-types/{type_id}:dry-run",
    response_model=DryRunOut,
    openapi_extra=readonly_extra("history"),
    summary="只读校验真实提交条件，不调用外部或创建动作",
)
async def dry_run(
    type_id: Annotated[str, Path(pattern=r"^[^\x00]+$", max_length=64)],
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    body: DryRunIn = Body(default_factory=DryRunIn),
    view: str = "published",
    cr: str | None = Query(None, max_length=64),
) -> dict:
    if cr is not None and "\x00" in cr:
        from app.core.errors import AppError

        raise AppError("变更单编号无效", code="VALIDATION_FAILED", status_code=422)
    value = await definition(session, type_id, ctx, view, cr)
    return await DatabaseSubmissionChecker(session, user).run(value, {"params": body.params})


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
    """参数默认10条。"""
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
    result, groups = await ActionTypeService(session).search_page(ctx, page, group, kw)
    return ActionTypeListOut(
        items=[ActionTypeOut.model_validate(r) for r in result.items],
        groups=groups,
        total=result.total,
        page=result.page,
        size=result.size,
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
    result = await ActionTypeService(session).params_page(ctx, type_id, page)
    return Page.of([ParamOut.model_validate(r) for r in result.items], result.total, page)


@router.get(
    "/action-types/{type_id}/stats",
    summary="当前类型近N日执行记录的真实聚合",
    dependencies=[Depends(require_func("action-center", "view"))],
)
async def action_stats(
    type_id: str, session: SessionDep, days: Annotated[int, Query(ge=1, le=90)] = 30
) -> dict:
    return await ActionTypeService(session).stats(type_id, days)


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


@router.get(
    "/action-types/{type_id}/used-by", response_model=ItemsOut[UsedByOut], summary="引用规则与实时告警统计"
)
async def used_by(
    type_id: Annotated[str, Path(pattern=r"^[^\x00]+$")],
    session: SessionDep,
    ctx: ViewContextDep,
) -> ItemsOut[UsedByOut]:
    rows = await ActionTypeService(session).used_by(ctx, type_id)
    return ItemsOut[UsedByOut](items=[UsedByOut.model_validate(r) for r in rows])
