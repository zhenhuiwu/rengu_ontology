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
角色页（Roles.vue，api-index §4.2）：/roles、/roles/meta、/roles/{id}（PATCH/DELETE）、/permissions、/scopes、
/members、/users/{id}/effective-access；
进入预览 /preview-sessions 属 §1.11（PDE），不在这里。
只读（?ver= / ?previewAs=）由全局 ReadonlyGuardDep 拦截写请求。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.common.auth import CurrentUser, CurrentUserDep, require_func
from app.common.pagination import MAX_PAGE_SIZE, PageParamsDep
from app.core.schema import DateTimeSec
from app.db.session import SessionDep
from app.modules.admin.models import SysUser
from app.modules.admin.roles import RoleBundle, RoleService, roles_meta, tenant_options
from app.modules.admin.schemas import (
    AdminUserOut,
    AdminUserPage,
    AuditCursorOut,
    AuditExportIn,
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


@router.get(
    "/roles/meta", response_model=RolesMetaOut, summary="页面常量", dependencies=[Depends(_roles_view)]
)
async def get_roles_meta(session: SessionDep) -> RolesMetaOut:
    """perm_objects 读本体对象类型 + 动作类型（本体为空时用 mock 清单兜底）。"""
    return RolesMetaOut.model_validate(await roles_meta(session))


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
    return RoleMemberPage.of([user_out(u) for u in rows], total, page)


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
