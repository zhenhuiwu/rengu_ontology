"""admin（负责人 PDE工程师）。挂载在 /api/admin/v1 下（app/api.py）。

本分支只实现审计日志页（src/pages/admin/AuditLog.vue，api-index §4.3）：
- GET  /audit-logs          游标分页 CursorResult<AuditRow>，from/to 必填，keyset (time desc, id desc)
- GET  /audit-logs/actions  动作下拉（去重，string[]，不含「全部」）
- POST /audit-logs/export   固定导出成员清单，返回 {jobId}，并写一条「导出」审计
- GET /export-jobs/{id}、/download  创建者查询 / 下载固定清单 CSV
用户页（src/pages/admin/Users.vue，api-index §4.1）：
- GET  /users                    页码分页 PageResult<AdminUser & {pendingApprovals}>，顺序 = mock ADMIN_USERS
- GET  /depts                    部门下拉（mock ADMIN_DEPTS 原顺序）
- POST /users、PUT /users/{id}   新建 / 编辑
- POST /users:import-preview     解析 + 校验，不写库；POST /users:import 导入
- POST /users/{id}:enable、POST /users/{id}:disable {transferTo?}
角色页（Roles.vue，api-index §4.2）：/roles、/roles/meta、/roles/{id}（PATCH/DELETE）、/permissions、/scopes、
/members、/users/{id}/effective-access；
进入预览 /preview-sessions 属 §1.11（PDE），不在这里。
只读（?ver= / ?previewAs=）由全局 ReadonlyGuardDep 拦截写请求。
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.auth import CurrentUser, CurrentUserDep, require_func
from app.common.context import ViewContextDep, check_writable
from app.common.pagination import MAX_PAGE_SIZE, PageParams, PageParamsDep
from app.core.config import api_prefix
from app.core.schema import DateTimeSec
from app.db.session import SessionDep
from app.modules.admin import exports
from app.modules.admin.imports import parse_import_xlsx
from app.modules.admin.models import SysUser
from app.modules.admin.repo import AuditCursor
from app.modules.admin.roles import RoleBundle, RoleService, roles_meta, tenant_options
from app.modules.admin.schemas import (
    AdminUserOut,
    AdminUserPage,
    AuditCursorOut,
    AuditExportId,
    AuditExportIn,
    AuditExportJobOut,
    AuditExportOut,
    AuditRowOut,
    DisableIn,
    EffectiveAccessOut,
    ImportIn,
    ImportOut,
    ImportPreviewIn,
    ImportPreviewOut,
    MembersAddIn,
    MembersAddOut,
    MeRowScopeOut,
    ObjPermIo,
    ResultFilter,
    RoleCreateIn,
    RoleMemberPage,
    RoleOut,
    RolePermissionsIn,
    RolesMetaOut,
    RoleUpdateIn,
    RowScopeIn,
    RowScopeOut,
    SearchField,
    StatusFilter,
    TenantOptionOut,
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


@router.get("/me", summary="当前真实身份、角色与发布能力")
async def current_profile(session: SessionDep, user: CurrentUserDep) -> dict:
    from sqlalchemy import select

    from app.modules.admin.models import SysRole

    roles = list(
        await session.scalars(select(SysRole.name).where(SysRole.code.in_(user.roles)).order_by(SysRole.code))
    )
    manage = user.func.get("releases") == "manage"
    return {
        "id": user.id,
        "name": user.name,
        "roles": list(user.roles),
        "roleNames": roles,
        "func": user.func,
        "capabilities": {"publish": manage and user.is_ontology_admin, "rollback": manage},
    }


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
    ontology_id: Annotated[int | None, Query(alias="ontologyId", ge=1)] = None,
) -> AuditCursorOut:
    f = make_filter(from_, to, action, result, field, kw, ontology_id)
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


@router.post("/audit-logs/export", response_model=AuditExportOut, summary="创建审计导出固定清单")
async def export_audit_logs(
    body: AuditExportIn,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(_audit_view)],
) -> AuditExportOut:
    f = make_filter(body.from_, body.to, body.action, body.result, body.field, body.kw, body.ontology_id)
    job_id = await AuditLogService(session).export(f, user, client_ip(request))
    return AuditExportOut(job_id=job_id)


@router.get("/export-jobs/{job_id}", response_model=AuditExportJobOut, summary="审计导出清单详情")
async def audit_export_job(
    job_id: AuditExportId,
    session: SessionDep,
    ctx: ViewContextDep,
    user: Annotated[CurrentUser, Depends(_audit_view)],
) -> AuditExportJobOut:
    check_writable(ctx.ver, ctx.preview_as)
    job = await exports.owned_job(session, job_id, user)
    return AuditExportJobOut(
        job_id=job.code,
        row_count=await exports.row_count(session, job.id),
        download_url=f"{api_prefix('admin')}/export-jobs/{job.code}/download",
    )


@router.get("/export-jobs/{job_id}/download", summary="下载审计 CSV（固定成员，分批流式）")
async def download_audit_export(
    job_id: AuditExportId,
    session: SessionDep,
    ctx: ViewContextDep,
    user: Annotated[CurrentUser, Depends(_audit_view)],
) -> StreamingResponse:
    check_writable(ctx.ver, ctx.preview_as)
    job = await exports.owned_job(session, job_id, user)
    # 首批复用请求连接：池满时不会每个请求占一条连接再等待第二条。
    rows = await exports.read_batch(session, job.id, None)
    first = exports.csv_batch(rows, header=True)
    cursor = AuditCursor.of(rows[-1]) if rows else None
    bind = session.bind
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    assert engine is not None
    return StreamingResponse(
        exports.stream_csv(engine, job.id, first, cursor, len(rows) == exports.BATCH_SIZE),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{job.code}.csv"',
            "Cache-Control": "no-store",
        },
    )


# ---- 用户 ----

_users_view = require_func("users", "view")
_users_manage = require_func("users", "manage")
UsersManage = Annotated[CurrentUser, Depends(_users_manage)]


def user_out(u: SysUser, pending: int = 0) -> AdminUserOut:
    return AdminUserOut.model_validate(u).model_copy(update={"pending_approvals": pending})


async def user_with_pending(session, user):
    counts = await pending_approvals(session, [user])
    return user_out(user, counts.get(user.code, 0))


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
    counts = await pending_approvals(session, list(rows))
    return AdminUserPage.of([user_out(u, counts.get(u.code, 0)) for u in rows], total, page)


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
    return await user_with_pending(session, await UserService(session).update(user_id, body, user))


@router.post("/users:import-preview", response_model=ImportPreviewOut, summary="导入预览（只校验，不写库）")
async def import_preview(body: ImportPreviewIn, session: SessionDep, _user: UsersManage) -> ImportPreviewOut:
    if body.rows is not None:
        rows = body.rows
    elif body.xlsx_base64 is not None:
        rows = await asyncio.to_thread(parse_import_xlsx, body.xlsx_base64)
    else:
        rows = await asyncio.to_thread(parse_import_csv, body.csv or "")
    return ImportPreviewOut(rows=await UserService(session).preview(rows))


@router.post("/users:import", response_model=ImportOut, summary="导入用户（新建为待激活，有错的行跳过）")
async def import_users(body: ImportIn, session: SessionDep, user: UsersManage) -> ImportOut:
    return ImportOut(imported=await UserService(session).import_rows(body.rows, user))


@router.post("/users/{user_id}:enable", response_model=AdminUserOut, summary="启用用户")
async def enable_user(user_id: str, session: SessionDep, user: UsersManage) -> AdminUserOut:
    return await user_with_pending(session, await UserService(session).enable(user_id, user))


@router.post(
    "/users/{user_id}:disable",
    response_model=AdminUserOut,
    summary="停用用户（有待办审批时 transferTo 必填）",
)
async def disable_user(
    user_id: str, session: SessionDep, user: UsersManage, body: DisableIn | None = None
) -> AdminUserOut:
    return user_out(await UserService(session).disable(user_id, body.transfer_to if body else None, user))


# ---- 角色与权限（Roles.vue，api-index §4.2；逻辑在 admin/roles.py）----

_roles_view = require_func("roles", "view")
_roles_manage = require_func("roles", "manage")
RolesManage = Annotated[CurrentUser, Depends(_roles_manage)]


def role_out(b: RoleBundle) -> RoleOut:
    return RoleOut.model_validate(b.role).model_copy(
        update={
            "scopes": [RowScopeOut.model_validate(s) for s in b.scopes],
            "member_count": b.member_count,
            "objs": [ObjPermIo.model_validate(o) for o in b.role.objs],
        }
    )


@router.get(
    "/roles",
    response_model=list[RoleOut],
    summary="角色列表（含成员数）",
    dependencies=[Depends(_roles_view)],
)
async def list_roles(session: SessionDep) -> list[RoleOut]:
    return [role_out(b) for b in await RoleService(session).list_all()]


@router.get("/roles/state", dependencies=[Depends(_roles_view), Depends(_users_view)])
async def role_edit_state(session: SessionDep):
    roles, members, users = await RoleService(session).state()

    def profile(user):
        return AdminUserOut.model_validate(user).model_dump(
            mode="json", by_alias=True, exclude={"pending_approvals"}
        )

    return {
        "roles": [role_out(role) for role in roles],
        "members": {code: [profile(user) for user in rows] for code, rows in members.items()},
        "users": [profile(user) for user in users],
    }


@router.get(
    "/roles/object-permissions",
    dependencies=[Depends(_roles_view), Depends(_users_view), Depends(require_func("object-types", "view"))],
)
async def object_role_permissions(
    session: SessionDep,
    object_id: Annotated[
        str, Query(alias="objectId", min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    ],
    page: int = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 10,
):
    params = PageParams(page=page, size=size)
    obj, items, total = await RoleService(session).object_permissions(object_id, params)
    for item in items:
        item["scopes"] = [
            RowScopeOut.model_validate(s).model_dump(mode="json", by_alias=True) for s in item["scopes"]
        ]
    return {"object": obj, "items": items, "total": total, "page": params.clamp(total).page, "size": size}


@router.get(
    "/roles/meta", response_model=RolesMetaOut, summary="页面常量", dependencies=[Depends(_roles_view)]
)
async def get_roles_meta(session: SessionDep, user: CurrentUserDep) -> RolesMetaOut:
    """对象与范围属性读本体，范围取值遵守当前用户行级过滤；空本体用 mock 兜底。"""
    return RolesMetaOut.model_validate(await roles_meta(session, user))


@router.get(
    "/roles/{role_id}", response_model=RoleOut, summary="角色详情", dependencies=[Depends(_roles_view)]
)
async def get_role(role_id: str, session: SessionDep) -> RoleOut:
    return role_out(await RoleService(session).get(role_id))


@router.post("/roles", response_model=RoleOut, summary="新建 / 复制角色（角色名唯一；复制不带成员）")
async def create_role(
    body: RoleCreateIn, request: Request, session: SessionDep, user: RolesManage
) -> RoleOut:
    b = await RoleService(session).create(
        name=body.name,
        desc=body.desc,
        perms=body.perms,
        copy_from=body.copy_from,
        user=user,
        ip=client_ip(request),
    )
    return role_out(b)


@router.patch("/roles/{role_id}", response_model=RoleOut, summary="改名称和说明（内置角色 403）")
async def update_role(
    role_id: str, body: RoleUpdateIn, request: Request, session: SessionDep, user: RolesManage
) -> RoleOut:
    b = await RoleService(session).update(
        role_id,
        name=body.name,
        desc=body.desc,
        lock_version=body.lock_version,
        user=user,
        ip=client_ip(request),
    )
    return role_out(b)


@router.put(
    "/roles/{role_id}/permissions", response_model=RoleOut, summary="保存功能与对象权限（内置角色 403）"
)
async def put_role_permissions(
    role_id: str, body: RolePermissionsIn, request: Request, session: SessionDep, user: RolesManage
) -> RoleOut:
    b = await RoleService(session).set_permissions(
        role_id,
        fn=dict(body.func),
        objs=[o.model_dump(by_alias=True) for o in body.objs],
        lock_version=body.lock_version,
        user=user,
        ip=client_ip(request),
    )
    return role_out(b)


@router.delete("/roles/{role_id}", status_code=204, summary="删除角色（内置角色 403；成员、范围一起删）")
async def delete_role(role_id: str, request: Request, session: SessionDep, user: RolesManage) -> Response:
    await RoleService(session).delete(role_id, user, client_ip(request))
    return Response(status_code=204)


@router.post(
    "/roles/{role_id}/scopes", response_model=RowScopeOut, summary="添加行级范围（属性须是权限依据）"
)
async def add_role_scope(
    role_id: str, body: RowScopeIn, request: Request, session: SessionDep, user: RolesManage
) -> RowScopeOut:
    s = await RoleService(session).add_scope(
        role_id,
        obj_type=body.obj_type,
        prop=body.prop,
        op=body.op,
        values=body.values,
        user=user,
        ip=client_ip(request),
    )
    return RowScopeOut.model_validate(s)


@router.delete("/roles/{role_id}/scopes/{scope_id}", status_code=204, summary="删除行级范围")
async def delete_role_scope(
    role_id: str, scope_id: str, request: Request, session: SessionDep, user: RolesManage
) -> Response:
    await RoleService(session).remove_scope(role_id, scope_id, user, client_ip(request))
    return Response(status_code=204)


@router.get(
    "/roles/{role_id}/members",
    response_model=RoleMemberPage,
    summary="角色成员（页码分页；kw 搜姓名 / 账号 / 用户 ID）",
    dependencies=[Depends(_roles_view)],
)
async def list_role_members(
    role_id: str,
    session: SessionDep,
    page: PageParamsDep,
    kw: Annotated[str | None, Query(description="包含（区分大小写）")] = None,
) -> RoleMemberPage:
    rows, total = await RoleService(session).members(role_id, kw, page)
    counts = await pending_approvals(session, list(rows))
    return RoleMemberPage.of([user_out(u, counts.get(u.code, 0)) for u in rows], total, page)


@router.post("/roles/{role_id}/members", response_model=MembersAddOut, summary="添加成员（已是成员的跳过）")
async def add_role_members(
    role_id: str, body: MembersAddIn, request: Request, session: SessionDep, user: RolesManage
) -> MembersAddOut:
    n = await RoleService(session).add_members(role_id, body.user_ids, user, client_ip(request))
    return MembersAddOut(added=n)


@router.delete("/roles/{role_id}/members/{user_id}", status_code=204, summary="移出角色")
async def remove_role_member(
    role_id: str, user_id: str, request: Request, session: SessionDep, user: RolesManage
) -> Response:
    await RoleService(session).remove_member(role_id, user_id, user, client_ip(request))
    return Response(status_code=204)


@router.get(
    "/users/{user_id}/effective-access",
    response_model=EffectiveAccessOut,
    summary="用户的有效权限（所有角色合并，预览抽屉用）",
    dependencies=[Depends(_roles_view)],
)
async def get_effective_access(user_id: str, session: SessionDep) -> EffectiveAccessOut:
    rs, eff = await RoleService(session).effective_access(user_id)
    return EffectiveAccessOut.model_validate({**eff, "rs": [role_out(b) for b in rs]})


@router.get("/me/row-scope", response_model=MeRowScopeOut, summary="当前用户逐对象类型的行级范围")
async def get_my_row_scope(user: CurrentUserDep, session: SessionDep) -> MeRowScopeOut:
    items = await RoleService(session).row_scope(user.id)
    return MeRowScopeOut.model_validate({"user_id": user.id, "user_name": user.name, "items": items})


@router.get("/tenants", response_model=list[TenantOptionOut], summary="顶栏租户 / 组织下拉")
async def list_tenants(_user: CurrentUserDep, session: SessionDep) -> list[TenantOptionOut]:
    return [TenantOptionOut.model_validate(x) for x in await tenant_options(session)]


from app.modules.admin.tenant_settings import router as tenant_settings_router  # noqa: E402

router.include_router(tenant_settings_router)
