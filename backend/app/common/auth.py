"""身份与权限（EXTENSION POINT (PDE)）。

业务模块的用法（签名定死，实现由 PDE 替换）：

    @router.get(...)
    async def handler(user: CurrentUserDep): ...

    # 查业务对象时加行级条件（表达式结构和转 SQL 见 app/common/row_filter.py）
    flt = await row_filter(user, "store")   # None = 不限制

现在的桩实现：
- dev/test 环境：读请求头 `X-Demo-As`（只在 dev/test 生效的演示身份），没有就用配置里的 RENGU_DEV_USER；
  **值是用户编号 sys_user.code（前端 userId，如 U010001），不是姓名**。按编号查 sys_user：
  CurrentUser.id = 编号，name = 姓名；查不到或已停用 → 401。
  演示身份：王敏 U010001（admin，mock 审计日志里的编号）、李娜 U010006（viewer，[推断] mock 没有编号）；
  本体管理员 = 配置 RENGU_DEMO_ADMIN_CODES（默认 ["U010001"]）。
- prod 环境：`X-Demo-As` 一律忽略；鉴权未接入前直接 401（防止桩实现被带上生产）。

租户（一期单租户）：接受 `X-Tenant-Id` 请求头并放进 CurrentUser.tenant_id，**不按租户过滤，表里也没有
tenant_id 列**。二期多租户时再统一加。
"""

from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import Unauthorized
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


async def resolve_user(session: AsyncSession, code: str, tenant_id: str | None = None) -> CurrentUser:
    """用户编号（sys_user.code）→ CurrentUser。查不到 / 停用 → 401。"""
    from app.modules.admin.models import SysUser, UserStatus

    u = await session.scalar(select(SysUser).where(SysUser.code == code))
    if u is None or u.status != UserStatus.active:
        raise Unauthorized("用户不存在或已停用", details={"userId": code})
    admin = code in get_settings().demo_admin_codes
    return CurrentUser(id=u.code, name=u.name, is_ontology_admin=admin, tenant_id=tenant_id)


async def get_current_user(
    session: SessionDep,
    x_demo_as: Annotated[
        str | None, Header(description="dev/test 演示身份：用户编号（U010001）；prod 忽略")
    ] = None,
    x_tenant_id: Annotated[str | None, Header(description="一期单租户：只透传，不过滤")] = None,
) -> CurrentUser:
    # EXTENSION POINT (PDE): 换成真实鉴权（会话 / API Key / 服务账号），解析出有效权限 EffectiveAccess。
    s = get_settings()
    if s.env == "prod":
        raise Unauthorized("鉴权尚未接入")
    return await resolve_user(session, x_demo_as or s.dev_user, x_tenant_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def row_filter(user: CurrentUser, object_type: str) -> dict | None:
    """返回该用户对某对象类型的行级过滤表达式（jsonb 结构见 app/common/row_filter.py）。

    None = 不限制（只有某个角色明确配置为「全部」时；桩实现里只有本体管理员）；
    没有任何角色范围 → combine_or([]) = DENY_ALL（拒绝）。

    查询层：`stmt.where(to_sql(await row_filter(user, "store"), {"region": Store.region, ...}, user.attrs))`
    """
    # EXTENSION POINT (PDE): 读该用户各角色的 ROLE_OBJECT_SCOPE.row_filter，用 combine_or 合并；
    # 权限范围里的中文属性名（如「大区」）按 sql_guard.GovernedTable.field_of_prop
    # 转成字段（from_row_scope）。
    # 桩实现：只有本体管理员不限制，其他人一律 DENY_ALL（宁可看不到，不能看多）。
    from app.common.row_filter import DENY_ALL

    return None if user.is_ontology_admin else DENY_ALL


def require_func(page_key: str, level: str = "view"):
    """功能权限依赖：`dependencies=[Depends(require_func("ontology.releases", "manage"))]`。"""

    async def _dep(user: CurrentUserDep) -> CurrentUser:
        # EXTENSION POINT (PDE): 校验 user.func[page_key] >= level，不满足抛 Forbidden。
        return user

    return _dep
