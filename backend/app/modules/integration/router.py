"""integration（负责人 Palantir专家）。只有一个路由，挂在 /api/data/v1（见 app/api.py）：

- 外部系统 /ext-systems*、回写调用 /writeback-calls*
  （action-event-api.md:140、:168，PDE 定外部系统属数据分组）。
- integration **不在 /api/ontology/v1 下开任何路由**（Palantir 定）：动作类型、事件类型、事件、对账等
  /api/ontology/v1 接口由 action / event 模块的路由提供，执行记录由 dynamic 提供。

冒号动作按 PDE api-index：`POST /ext-systems/{id}:test` 等。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Request
from pydantic import Field

from app.common.auth import CurrentUser, CurrentUserDep, check_func, require_func
from app.common.pagination import MAX_PAGE_SIZE, PageParams
from app.core.schema import ApiModel
from app.db.session import SessionDep
from app.modules.integration.schemas import (
    ExtCallOut,
    ExtCallPageOut,
    ExtSystemDetailOut,
    ExtSystemIn,
    ExtSystemListOut,
    ExtSystemOut,
    ExtSystemTestIn,
    ExtSystemTestOut,
    LinkedActionTypeOut,
)
from app.modules.integration.service import ExtSystemService, WritebackCallService

router = APIRouter(tags=["integration"])  # /api/data/v1


class EffectRetryIn(ApiModel):
    attempts: int = Field(ge=0)
    state: Literal["failed", "rejected"]


class ActionAuthorizationItem(ApiModel):
    id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=16)
    path: str = Field(min_length=1, max_length=255)


class ActionAuthorizationIn(ApiModel):
    version: int = Field(ge=1)
    actions: list[ActionAuthorizationItem] = Field(max_length=100)


@router.get("/ext-systems/{sys_id}/action-authorizations")
async def get_action_authorizations(sys_id: str, session: SessionDep, user: CurrentUserDep):
    from sqlalchemy import select
    from app.modules.action.models import ActionType, TypeStatus
    from app.modules.integration.adapters import get_adapter
    check_func(user, "external-systems", "manage")
    check_func(user, "action-types", "manage")
    system = await ExtSystemService(session).repo.get_by_code_or_404(sys_id)
    adapter = get_adapter(system, "submit")
    actions = await session.scalars(select(ActionType).where(
        ActionType.sys_id == system.id, ActionType.status == TypeStatus.published,
        ActionType.retired_release_id.is_(None),
    ).order_by(ActionType.code))
    from app.modules.integration.adapters import local_demo_config
    target = local_demo_config(system.ontology_id, system.code) if system.local_only else None
    paths = [target.submit_path] if target else sorted(set(adapter.config.submit_paths) | {adapter.config.submit_path})
    return {"version": system.lock_version, "paths": paths,
            "actions": [{"id": a.code, "name": a.name, "version": a.version} for a in actions],
            "selected": [{"id": code, **entry} for code, entry in (system.action_authorizations or {}).items() if isinstance(entry, dict)]}


@router.put("/ext-systems/{sys_id}/action-authorizations")
async def save_action_authorizations(sys_id: str, body: ActionAuthorizationIn, session: SessionDep, user: CurrentUserDep, request: Request):
    from sqlalchemy import select
    from app.core.errors import AppError, VersionConflict
    from app.modules.action.models import ActionType, TypeStatus
    from app.modules.integration.adapters import get_adapter, local_demo_config
    from app.modules.integration.models import ExtSystem
    from app.db.dialects import get_adapter as db_adapter
    check_func(user, "external-systems", "manage")
    check_func(user, "action-types", "manage")
    await db_adapter(session).xact_lock(session, "ontology.publish")
    system = await session.scalar(select(ExtSystem).where(ExtSystem.code == sys_id).with_for_update())
    if system is None:
        raise AppError("外部系统不存在", code="NOT_FOUND", status_code=404)
    if system.lock_version != body.version:
        raise VersionConflict()
    adapter = get_adapter(system, "submit")
    target = local_demo_config(system.ontology_id, system.code)
    if system.endpoint != adapter.origin or (target.origin if target else adapter.config.origin) != adapter.origin:
        raise AppError("部署未授权该系统的精确地址", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)
    available = {a.code: a.version for a in await session.scalars(select(ActionType).where(
        ActionType.sys_id == system.id, ActionType.status == TypeStatus.published,
        ActionType.retired_release_id.is_(None),
    ))}
    paths = {target.submit_path} if system.local_only and target else set(adapter.config.submit_paths) | {adapter.config.submit_path}
    if len(body.actions) != len({a.id for a in body.actions}) or any(
        available.get(a.id) != a.version or a.path not in paths for a in body.actions
    ):
        raise AppError("动作版本或目标路径不在允许范围", code="VALIDATION_FAILED", status_code=422)
    system.action_authorizations = {a.id: {"version": a.version, "path": a.path} for a in body.actions}
    from app.modules.admin.repo import insert_audit
    await insert_audit(session, actor=f"{user.name}（{user.id}）", action="修改外部系统动作授权",
                       target=(f"{system.code}: " + ", ".join(f"{a.id} {a.version} {a.path}" for a in body.actions))[:200],
                       ip=request.client.host if request.client else "", created_by=user.id)
    await session.flush()
    return {"version": system.lock_version}


@router.get("/action-runs/{code}/effects", dependencies=[Depends(require_func("external-systems", "view"))])
async def action_effects(
    code: str,
    session: SessionDep,
    user: CurrentUserDep,
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=MAX_PAGE_SIZE),
):
    from app.modules.integration.effect_management import list_effects

    return await list_effects(session, user, code, PageParams(page=page, size=size))


@router.post(
    "/action-runs/{code}/effects/{identity}:retry",
    dependencies=[Depends(require_func("external-systems", "manage"))],
)
async def retry_action_effect(
    code: str, identity: int, body: EffectRetryIn, session: SessionDep, user: CurrentUserDep, request: Request
):
    from app.modules.integration.effect_management import retry_effect

    return await retry_effect(
        session,
        user,
        code,
        identity,
        body.attempts,
        body.state,
        ip=request.client.host if request.client else "",
    )


_ext_view = require_func("external-systems", "view")
_action_view = require_func("action-types", "view")
_ext_manage = require_func("external-systems", "manage")


async def ext_list_access(
    user: CurrentUserDep,
    writeback: Annotated[int, Query(ge=0, le=1, description="1 = 只要可写回的（生产环境且未停用）")] = 0,
) -> tuple[CurrentUser, int]:
    """external-systems · view；`writeback=1`（动作类型写回 tab 选系统用）有 action-types · view 也可以。

    EXTENSION POINT (PDE)：require_func 现在是桩；落地后这里改成「两者满足其一」。
    """
    return await (_action_view if writeback else _ext_view)(user), writeback


@router.get("/ext-systems", response_model=ExtSystemListOut, summary="外部系统列表")
async def list_ext_systems(
    request: Request,
    session: SessionDep,
    access: Annotated[tuple[CurrentUser, int], Depends(ext_list_access)],
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
    # [推断] 前端外部系统页没有搜索框，kw 是后端补的可选过滤（名称 / 编号 / 厂商包含），不传 = 全部
    kw: Annotated[str | None, Query(description="名称 / 编号 / 厂商包含")] = None,
) -> ExtSystemListOut:
    _user, writeback = access  # writeback 只在依赖里声明一次，参数错误只报一条
    svc = ExtSystemService(session)
    if writeback and "page" not in request.query_params and "size" not in request.query_params:
        rows = await svc.search(writeback=True, kw=kw)
        total, current_page, current_size = len(rows), 1, max(1, len(rows))
    else:
        params = PageParams(page=page, size=size)
        rows, total = await svc.page(writeback=bool(writeback), kw=kw, page=params)
        current_page, current_size = params.clamp(total).page, params.size
    counts = await svc.action_counts([row.id for row in rows])
    items = [
        ExtSystemOut.model_validate(r).model_copy(update={"action_count": counts.get(r.id, 0)}) for r in rows
    ]
    return ExtSystemListOut(items=items, total=total, page=current_page, size=current_size)


@router.get(
    "/ext-systems/{sys_id}",
    response_model=ExtSystemDetailOut,
    summary="外部系统详情（含关联的动作类型）",
    dependencies=[Depends(_ext_view)],
)
async def get_ext_system(sys_id: str, session: SessionDep) -> ExtSystemDetailOut:
    s, actions = await ExtSystemService(session).get(sys_id)
    out = ExtSystemDetailOut.model_validate(s)
    out.action_types = [LinkedActionTypeOut.model_validate(a) for a in actions]
    return out


@router.post("/ext-systems", response_model=ExtSystemDetailOut, summary="新建外部系统（直接生效）")
async def create_ext_system(
    body: ExtSystemIn, session: SessionDep, user: Annotated[CurrentUser, Depends(_ext_manage)]
) -> ExtSystemDetailOut:
    svc = ExtSystemService(session)
    s = await svc.create(body, actor=user.id)
    return await _detail(svc, s.code)


@router.post("/ext-systems/{sys_id}:test", response_model=ExtSystemTestOut, summary="测试调用（连通性探测）")
async def test_ext_system(
    sys_id: str,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(_ext_manage)],
    body: Annotated[ExtSystemTestIn | None, Body()] = None,
) -> ExtSystemTestOut:
    rec = await ExtSystemService(session).test_call(sys_id, f"{user.name}（{user.id}）")
    return ExtSystemTestOut.model_validate(rec)


# 注意：要排在 :test 之后注册，否则 POST /ext-systems/EXT-X:test 会被 {sys_id} 吃掉。
# 文档写的是 PUT；前端 apiClient 已有 put（palantir-r2 起 src/api/integration.ts 用 PUT）。
# POST /ext-systems/{id} 保留为同义别名（旧前端兼容），两个方法行为完全一样。
@router.put("/ext-systems/{sys_id}", response_model=ExtSystemDetailOut, summary="编辑外部系统")
async def update_ext_system(
    sys_id: str, body: ExtSystemIn, session: SessionDep, user: Annotated[CurrentUser, Depends(_ext_manage)]
) -> ExtSystemDetailOut:
    svc = ExtSystemService(session)
    await svc.update(sys_id, body, actor=user.id)
    return await _detail(svc, sys_id)


@router.post("/ext-systems/{sys_id}", response_model=ExtSystemDetailOut, summary="编辑外部系统（POST 同义）")
async def update_ext_system_post(
    sys_id: str, body: ExtSystemIn, session: SessionDep, user: Annotated[CurrentUser, Depends(_ext_manage)]
) -> ExtSystemDetailOut:
    return await update_ext_system(sys_id, body, session, user)


async def _detail(svc: ExtSystemService, code: str) -> ExtSystemDetailOut:
    s, actions = await svc.get(code)
    out = ExtSystemDetailOut.model_validate(s)
    out.action_types = [LinkedActionTypeOut.model_validate(a) for a in actions]
    return out


# ---------------------------------------------------------------- 回写调用（外部系统页「最近调用」）

_RESULT_Q = Query(description="成功 / 失败 / 结果未知；全部 / 不传 = 不过滤")
CallResultQ = Literal["全部", "成功", "失败", "结果未知"]


def _calls_page(
    page: Annotated[int, Query()] = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
) -> PageParams:
    """前端「最近调用」每页 10 条（ExternalSystems.vue SIZE）；page ≤ 0 收成 1。"""
    return PageParams(page=page, size=size)


@router.get(
    "/ext-systems/{sys_id}/calls",
    response_model=ExtCallPageOut,
    summary="某外部系统的最近调用（按结果筛选，分页）",
    dependencies=[Depends(_ext_view)],
)
async def list_ext_system_calls(
    sys_id: str,
    session: SessionDep,
    page: Annotated[PageParams, Depends(_calls_page)],
    result: Annotated[CallResultQ | None, _RESULT_Q] = None,
) -> dict:
    return await WritebackCallService(session).search(sys_code=sys_id, result=result, page=page)


@router.get(
    "/writeback-calls",
    response_model=ExtCallPageOut,
    summary="回写调用记录（按系统 / 结果筛选，分页）",
    dependencies=[Depends(_ext_view)],
)
async def list_writeback_calls(
    session: SessionDep,
    page: Annotated[PageParams, Depends(_calls_page)],
    sys_id: Annotated[str | None, Query(alias="sysId", description="外部系统编号；不传 = 全部系统")] = None,
    result: Annotated[CallResultQ | None, _RESULT_Q] = None,
) -> dict:
    return await WritebackCallService(session).search(sys_code=sys_id, result=result, page=page)


@router.get(
    "/writeback-calls/{call_id}",
    response_model=ExtCallOut,
    summary="调用详情（抽屉 ?call=）",
    dependencies=[Depends(_ext_view)],
)
async def get_writeback_call(call_id: str, session: SessionDep) -> dict:
    return await WritebackCallService(session).get(call_id)


@router.post(
    "/writeback-calls/{call_id}:mark-checked",
    response_model=ExtCallOut,
    summary="人工核对 · 确认对方已处理（只限结果未知、无执行记录的调用）",
)
async def mark_writeback_call_checked(
    call_id: str, session: SessionDep, user: Annotated[CurrentUser, Depends(_ext_manage)]
) -> dict:
    return await WritebackCallService(session).mark_checked(call_id, user.name)
