"""身份与权限（EXTENSION POINT (PDE)）。

业务模块的用法（签名定死，实现由 PDE 替换）：

    @router.get(...)
    async def handler(user: CurrentUserDep): ...

    # 查业务对象时加行级条件（表达式结构和转 SQL 见 app/common/row_filter.py）
    flt = await row_filter(user, "store")   # None = 不限制

身份入口（功能权限已按实际数据库角色生效）：
- dev/test 环境：读请求头 `X-Demo-As`（只在 dev/test 生效的演示身份），没有就用配置里的 RENGU_DEV_USER；
  **值是用户编号 sys_user.code（前端 userId，如 U010001），不是姓名**。按编号查 sys_user：
  CurrentUser.id = 编号，name = 姓名；查不到或已停用 → 401。
  演示身份：王敏 U010001（admin，mock 审计日志里的编号）、李娜 U010006（viewer，[推断] mock 没有编号）；
  本体管理员 = 配置 RENGU_DEMO_ADMIN_CODES（默认 ["U010001"]）。
  此标志只用于已有发布管理员限制，不授予功能权限；缺少DB角色关联时功能权限为空。
- prod 环境：`X-Demo-As` 一律忽略；鉴权未接入前直接 401（防止桩实现被带上生产）。

租户（一期单租户）：接受 `X-Tenant-Id` 请求头并放进 CurrentUser.tenant_id，**不按租户过滤，表里也没有
tenant_id 列**。二期多租户时再统一加。
"""

from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Forbidden, Unauthorized
from app.db.session import SessionDep


@dataclass(frozen=True)
class CurrentUser:
    id: str
    name: str
    # 区权限：use / build / admin
    zones: frozenset[str] = frozenset({"use", "build", "admin"})
    # 功能权限：页面 key → none / view / manage（前端 Role.func）
    func: dict[str, str] = field(default_factory=dict)
    roles: tuple[str, ...] = ()
    is_ontology_admin: bool = False
    is_service_account: bool = False
    # 行级过滤表达式里 valueFrom: "user.<attr>" 取值的来源，例如 {"region": "华东"}
    attrs: dict[str, Any] = field(default_factory=dict)
    # 一期单租户：只透传（日志/审计用），不参与过滤
    tenant_id: str | None = None
    # Loaded once per identity from real role membership; absent objects are denied.
    row_filters: dict[str, dict | None] = field(default_factory=dict)


async def resolve_user(session: AsyncSession, code: str, tenant_id: str | None = None) -> CurrentUser:
    """用户编号（sys_user.code）→ CurrentUser。查不到 / 停用 → 401。"""
    from app.modules.admin.models import SysUser, UserStatus
    from app.modules.admin.permissions import function_access, object_row_access

    u = await session.scalar(
        select(SysUser).where(SysUser.code == code).execution_options(populate_existing=True)
    )
    if u is None or u.status != UserStatus.active:
        raise Unauthorized("用户不存在或已停用", details={"userId": code})
    admin = code in get_settings().demo_admin_codes
    permissions, roles = await function_access(session, u.id)
    return CurrentUser(
        id=u.code,
        name=u.name,
        func=permissions,
        roles=roles,
        row_filters=await object_row_access(session, u.id),
        is_ontology_admin=admin,
        tenant_id=tenant_id,
    )


async def get_current_user(
    session: SessionDep,
    request: Request,
    x_demo_as: Annotated[
        str | None, Header(description="dev/test 演示身份：用户编号（U010001）；prod 忽略")
    ] = None,
    x_tenant_id: Annotated[str | None, Header(description="一期单租户：只透传，不过滤")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if authorization is not None:
        from app.modules.admin.api_keys import authenticate_key

        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise Unauthorized("请使用 Bearer API 密钥")
        return await authenticate_key(session, token, request, x_tenant_id)
    # EXTENSION POINT (PDE): 换成真实鉴权（会话 / API Key / 服务账号），解析出有效权限 EffectiveAccess。
    s = get_settings()
    if s.env == "prod":
        raise Unauthorized("鉴权尚未接入")
    return await resolve_user(session, x_demo_as or s.dev_user, x_tenant_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def api_key_guard(request: Request, session: SessionDep) -> None:
    """Apply the token allowlist even to API handlers without a CurrentUser dependency."""
    authorization = request.headers.get("authorization")
    if authorization is not None:
        await get_current_user(
            session, request, x_tenant_id=request.headers.get("x-tenant-id"), authorization=authorization
        )


ApiKeyGuardDep = Depends(api_key_guard)


async def row_filter(user: CurrentUser, object_type: str) -> dict | None:
    """返回该用户对某对象类型的行级过滤表达式（jsonb 结构见 app/common/row_filter.py）。

    None = 实际角色授予且不限制；无角色/无对象授权/未知身份映射一律 DENY_ALL。

    查询层：`stmt.where(to_sql(await row_filter(user, "store"), {"region": Store.region, ...}, user.attrs))`
    """
    from app.common.row_filter import DENY_ALL

    return getattr(user, "row_filters", {}).get(object_type, dict(DENY_ALL))


def check_func(user: CurrentUser, page_key: str, level: str = "view") -> None:
    """请求身份已从DB聚合权限；缺失/未知级别按无权限，管理员标志不是功能授权旁路。"""
    ranks = {"none": 0, "view": 1, "manage": 2}
    if level not in ("view", "manage"):
        raise ValueError("功能权限只能检查 view/manage")
    actual = user.func.get(page_key, "none")
    if not isinstance(actual, str) or ranks.get(actual, 0) < ranks[level]:
        raise Forbidden("没有此页面的操作权限", details={"page": page_key, "required": level})


def require_func(page_key: str, level: str = "view"):
    """功能权限依赖：`dependencies=[Depends(require_func("releases", "manage"))]`。"""

    async def _dep(user: CurrentUserDep) -> CurrentUser:
        check_func(user, page_key, level)
        return user

    return _dep
