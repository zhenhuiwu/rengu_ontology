"""admin（负责人 PDE工程师）。挂载在 /api/admin/v1 下（app/api.py）。

本分支只实现审计日志页（src/pages/admin/AuditLog.vue，api-index §4.3）：
- GET  /audit-logs          游标分页 CursorResult<AuditRow>，from/to 必填，keyset (time desc, id desc)
- GET  /audit-logs/actions  动作下拉（去重，string[]，不含「全部」）
- POST /audit-logs/export   导出桩，返回 {jobId}，并写一条「导出」审计
用户页（src/pages/admin/Users.vue，api-index §4.1）：
- GET  /users                    页码分页 PageResult<AdminUser & {pendingApprovals}>，顺序 = mock ADMIN_USERS
- GET  /depts                    部门下拉（mock ADMIN_DEPTS 原顺序）
- POST /users、PUT /users/{id}   新建 / 编辑
- POST /users:import-preview     解析 + 校验，不写库；POST /users:import 导入
- POST /users/{id}:enable、POST /users/{id}:disable {transferTo?}
角色页留到下一阶段。只读（?ver= / ?previewAs=）由全局 ReadonlyGuardDep 拦截写请求。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.auth import CurrentUser, require_func
from app.common.pagination import MAX_PAGE_SIZE, PageParamsDep
from app.core.schema import DateTimeSec
from app.db.session import SessionDep
from app.modules.admin.models import SysUser
from app.modules.admin.schemas import (
    AdminUserOut,
    AdminUserPage,
    AuditCursorOut,
    AuditExportIn,
    AuditExportOut,
    AuditRowOut,
    DisableIn,
    ImportIn,
    ImportOut,
    ImportPreviewIn,
    ImportPreviewOut,
    ResultFilter,
    SearchField,
    StatusFilter,
    UserCreateIn,
    UserUpdateIn,
)
from app.modules.admin.service import (
    ADMIN_DEPTS,
    AuditLogService,
    UserService,
    make_filter,
    make_user_filter,
    parse_import_csv,
    pending_approvals,
)

router = APIRouter(tags=["admin"])

_audit_view = require_func("audit", "view")


def client_ip(request: Request) -> str:
    """来源 IP：有 X-Forwarded-For 取第一个，否则取连接地址。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


@router.get(
    "/audit-logs",
    response_model=AuditCursorOut,
    summary="审计日志（游标分页，按时间倒序）",
    dependencies=[Depends(_audit_view)],
)
async def list_audit_logs(
    session: SessionDep,
    from_: Annotated[DateTimeSec, Query(alias="from", description="起（含），YYYY-MM-DD HH:mm[:ss]")],
    to: Annotated[DateTimeSec, Query(description="止（含）")],
    action: Annotated[str | None, Query(description="动作；不传 / 全部 = 不限")] = None,
    result: Annotated[ResultFilter | None, Query(description="成功 / 失败 / 拒绝；全部 = 不限")] = None,
    field: Annotated[SearchField, Query(description="kw 搜哪个字段")] = "actor",
    kw: Annotated[str | None, Query(description="包含（区分大小写，先 trim）")] = None,
    cursor: Annotated[str | None, Query(description="上一页返回的 nextCursor；不传 = 第一页")] = None,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> AuditCursorOut:
    f = make_filter(from_, to, action, result, field, kw)
    rows, nxt = await AuditLogService(session).page(f, cursor or None, size)
    return AuditCursorOut(items=[AuditRowOut.model_validate(r) for r in rows], next_cursor=nxt)


@router.get(
    "/audit-logs/actions",
    response_model=list[str],
    summary="审计动作下拉（去重）",
    dependencies=[Depends(_audit_view)],
)
async def list_audit_actions(session: SessionDep) -> list[str]:
    return await AuditLogService(session).actions()


@router.post("/audit-logs/export", response_model=AuditExportOut, summary="导出审计日志（桩：只返回 jobId）")
async def export_audit_logs(
    body: AuditExportIn,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(_audit_view)],
) -> AuditExportOut:
    f = make_filter(body.from_, body.to, body.action, body.result, body.field, body.kw)
    job_id = await AuditLogService(session).export(f, user, client_ip(request))
    return AuditExportOut(job_id=job_id)


# ---- 用户 ----

_users_view = require_func("users", "view")
_users_manage = require_func("users", "manage")
UsersManage = Annotated[CurrentUser, Depends(_users_manage)]


def user_out(u: SysUser) -> AdminUserOut:
    return AdminUserOut.model_validate(u).model_copy(update={"pending_approvals": pending_approvals(u)})


@router.get(
    "/users",
    response_model=AdminUserPage,
    summary="用户列表（页码分页，含待办审批数）",
    dependencies=[Depends(_users_view)],
)
async def list_users(
    session: SessionDep,
    page: PageParamsDep,
    dept: Annotated[str | None, Query(description="部门全称；不传 / 全部 = 不限")] = None,
    status: Annotated[StatusFilter | None, Query(description="启用 / 停用 / 待激活；全部 = 不限")] = None,
    kw: Annotated[str | None, Query(description="姓名、账号或用户 ID 包含（区分大小写，不 trim）")] = None,
) -> AdminUserPage:
    rows, total = await UserService(session).page(make_user_filter(dept, status, kw), page)
    return AdminUserPage.of([user_out(u) for u in rows], total, page)


@router.get("/depts", response_model=list[str], summary="部门下拉", dependencies=[Depends(_users_view)])
async def list_depts() -> list[str]:
    return list(ADMIN_DEPTS)


@router.post("/users", response_model=AdminUserOut, summary="新建用户（账号唯一）")
async def create_user(body: UserCreateIn, session: SessionDep, user: UsersManage) -> AdminUserOut:
    return user_out(await UserService(session).create(body, user))


@router.put("/users/{user_id}", response_model=AdminUserOut, summary="编辑用户（账号不能改）")
async def update_user(
    user_id: str, body: UserUpdateIn, session: SessionDep, user: UsersManage
) -> AdminUserOut:
    return user_out(await UserService(session).update(user_id, body, user))


@router.post("/users:import-preview", response_model=ImportPreviewOut, summary="导入预览（只校验，不写库）")
async def import_preview(body: ImportPreviewIn, session: SessionDep, _user: UsersManage) -> ImportPreviewOut:
    rows = body.rows if body.rows is not None else parse_import_csv(body.csv or "")
    return ImportPreviewOut(rows=await UserService(session).preview(rows))


@router.post("/users:import", response_model=ImportOut, summary="导入用户（新建为待激活，有错的行跳过）")
async def import_users(body: ImportIn, session: SessionDep, user: UsersManage) -> ImportOut:
    return ImportOut(imported=await UserService(session).import_rows(body.rows, user))


@router.post("/users/{user_id}:enable", response_model=AdminUserOut, summary="启用用户")
async def enable_user(user_id: str, session: SessionDep, user: UsersManage) -> AdminUserOut:
    return user_out(await UserService(session).enable(user_id, user))


@router.post(
    "/users/{user_id}:disable",
    response_model=AdminUserOut,
    summary="停用用户（有待办审批时 transferTo 必填）",
)
async def disable_user(
    user_id: str, session: SessionDep, user: UsersManage, body: DisableIn | None = None
) -> AdminUserOut:
    return user_out(await UserService(session).disable(user_id, body.transfer_to if body else None, user))
