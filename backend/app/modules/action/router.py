"""动作类型只读接口（action-event-api §3.1），挂在 /api/ontology/v1。

全部支持 `?ver=`（读发布快照）和 `?previewAs=`（只读；行级过滤对定义类数据不适用）。
功能权限：action-types · view。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.common.auth import require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, Page
from app.db.session import SessionDep
from app.modules.action import sections as _sections  # noqa: F401  TEMP：import 即登记快照 section（骨架自动发现前）
from app.modules.action.schemas import (
    ActionTypeDetailOut,
    ActionTypeListOut,
    ActionTypeOut,
    CriterionOut,
    ItemsOut,
    ParamOut,
    TypeVersionOut,
    WritebackOut,
)
from app.modules.action.service import ActionTypeService, paginate

router = APIRouter(tags=["action"], dependencies=[Depends(require_func("action-types", "view"))])

PageNo = Annotated[int, Query(ge=1)]


@router.get(
    "/action-types", response_model=ActionTypeListOut, summary="动作类型列表（分组 / 关键字筛选，分页）"
)
async def list_action_types(
    session: SessionDep,
    ctx: ViewContextDep,
    group: Annotated[str | None, Query(description="分组（中文）；全部 / 不传 = 不过滤")] = None,
    kw: Annotated[str | None, Query(description="名称包含")] = None,
    page: PageNo = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 22,
) -> ActionTypeListOut:
    rows, groups = await ActionTypeService(session).search(ctx, group, kw)
    return ActionTypeListOut(
        items=[ActionTypeOut.model_validate(r) for r in paginate(rows, page, size)],
        groups=groups,
        total=len(rows),
        page=page,
        size=size,
    )


@router.get("/action-types/{type_id}", response_model=ActionTypeDetailOut, summary="动作类型详情（概览）")
async def get_action_type(type_id: str, session: SessionDep, ctx: ViewContextDep) -> ActionTypeDetailOut:
    return ActionTypeDetailOut.model_validate(await ActionTypeService(session).detail(ctx, type_id))


@router.get("/action-types/{type_id}/params", response_model=Page[ParamOut], summary="动作参数（分页）")
async def list_params(
    type_id: str,
    session: SessionDep,
    ctx: ViewContextDep,
    page: PageNo = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 12,
) -> Page[ParamOut]:
    rows = (await ActionTypeService(session).one(ctx, type_id)).get("params") or []
    items = [ParamOut.model_validate(r) for r in paginate(rows, page, size)]
    return Page(items=items, total=len(rows), page=page, size=size)


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
