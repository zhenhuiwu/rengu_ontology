"""身份与权限（EXTENSION POINT (PDE)）。

业务模块的用法（签名定死，实现由 PDE 替换）：

    @router.get(...)
    async def handler(user: CurrentUserDep): ...

    # 查业务对象时加行级条件（表达式结构和转 SQL 见 app/common/row_filter.py）
    flt = await row_filter(user, "store")   # None = 不限制

身份入口：真人使用数据库持久化 Cookie 会话；机器使用已授权 Bearer API Key。
X-Demo-As / 默认身份仅在显式启用、本地隔离测试库的 test 夹具中生效。
功能权限来自实际数据库角色，不按用户编号授予。

租户（一期单租户）：接受 `X-Tenant-Id` 请求头并放进 CurrentUser.tenant_id，**不按租户过滤，表里也没有
tenant_id 列**。二期多租户时再统一加。
"""

from dataclasses import dataclass, field, replace
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import api_prefix, get_settings
from app.core.errors import AppError, Forbidden, Unauthorized
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
    ontology_id: int | None = None
    ontology_role: str | None = None
    ontology_ids: tuple[int, ...] | None = (
        None  # API keys: persisted allowlist, then intersect current membership.
    )
    # 机器调用方（API 密钥 / 服务账号）上下文；真人会话全为 None。见 handoff/xiaobai.md「服务账号身份接口」。
    service_account_id: int | None = None  # ServiceAccount.id（编号即 id 字段）
    api_key_prefix: str | None = None
    api_scopes: tuple[str, ...] | None = None
    action_allowlist: tuple[str, ...] | None = None  # None = 不限；() = 全部拒绝
    event_allowlist: tuple[str, ...] | None = None
    function_allowlist: tuple[str, ...] | None = None
    # {"minute": {"limit": int|None, "used": int|None}, "day": {...}}：本次调用计数后的窗口用量
    quota: dict[str, dict[str, int | None]] | None = None


def full_route_path(request: Request) -> str:
    """带分组前缀的路由模板（如 `/api/admin/v1/users/{code}`）。

    FastAPI 0.162 起 scope["route"].path 是路由器相对路径（`/users/{code}`），不再带 include_router 前缀；
    这里用实际请求路径补回前缀。未匹配到路由时返回空串。
    """
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    regex = getattr(route, "path_regex", None)
    actual = request.scope.get("path", "")
    if regex is None or path.startswith("/api/") or regex.match(actual):
        return path
    for cut in range(1, len(actual)):
        if actual[cut] == "/" and regex.match(actual[cut:]):
            return actual[:cut] + path
    return path


def is_admin_request(request: Request) -> bool:
    """后台管理分组（/api/admin/v1/...）按实际请求路径判断，不依赖路由模板是否带前缀。"""
    prefix = api_prefix("admin")
    actual = request.scope.get("path", "")
    return actual == prefix or actual.startswith(prefix + "/")


def definition_only_request(request: Request) -> bool:
    """Audited definition readers need membership, not business-table reflection or row grants."""
    if request.method != "GET" or request.headers.get("authorization") is not None:
        return False
    route = full_route_path(request)
    if route in {
        api_prefix("admin") + path for path in (
            "/me", "/governance/my-access", "/depts", "/users/options", "/tenants",
            "/roles", "/roles/state", "/roles/{role_id}", "/roles/object-permissions",
            "/settings/tenant", "/settings/models",
        )
    }:
        return True
    if route in {
        api_prefix("data") + path for path in (
            "/datasources", "/datasources/meta", "/datasources/{ds_id}",
            "/datasources/{ds_id}/tables", "/datasources/{ds_id}/mappings",
            "/ext-systems", "/ext-systems/{sys_id}", "/ext-systems/{sys_id}/http-endpoints",
        )
    }:
        return True
    prefix = api_prefix("ontology")
    if route == prefix + "/change-requests":
        return request.query_params.get("workflow") == "true"
    return route in {
        prefix + path for path in (
            "/object-types", "/object-types/brief", "/object-types/{id}",
            "/object-types/{id}/properties", "/object-types/{id}/links", "/object-types/{id}/mappings",
            "/link-types", "/link-types/{id}", "/link-types/{id}/history",
            "/link-types/catalog", "/link-types/paths", "/ontology/graph",
            "/metrics", "/metrics/groups", "/metrics/{id}", "/metrics/{id}/versions",
            "/functions", "/functions/{code}", "/functions/{code}/versions", "/function-targets",
            "/action-types", "/action-types/{type_id}", "/action-types/{type_id}/params",
            "/action-types/{type_id}/submission-criteria", "/action-types/{type_id}/writeback",
            "/action-types/{type_id}/versions",
            # Event detail/subscribers mix definitions with permission-filtered runtime counts.
            "/event-types", "/event-types/{type_id}/payload-fields", "/event-types/{type_id}/versions",
            "/knowledge", "/knowledge/{id}", "/knowledge/synonym-conflicts",
            "/dimensions", "/dimensions/{id}", "/dimensions/{id}/values",
            "/dimensions/{id}/mappings", "/dimensions/{id}/history",
            "/change-requests/draft-targets",
            "/workspaces/current", "/releases", "/releases/current",
        )
    }


async def resolve_user(session: AsyncSession, code: str, tenant_id: str | None = None) -> CurrentUser:
    """用户编号（sys_user.code）→ CurrentUser。查不到 / 停用 → 401。"""
    from app.modules.admin.models import SysUser

    u = await session.scalar(
        select(SysUser).where(SysUser.code == code).execution_options(populate_existing=True)
    )
    if u is None:
        raise Unauthorized("用户不存在或已停用", details={"userId": code})
    return await _resolved_user(session, u, tenant_id)


async def _resolved_user(session: AsyncSession, u: Any, tenant_id: str | None = None) -> CurrentUser:
    """Build permissions from the user record already checked by this request's session lookup."""
    from app.modules.admin.models import UserStatus
    from app.modules.admin.permissions import function_access, object_row_access

    if u.status != UserStatus.active:
        raise Unauthorized("用户不存在或已停用", details={"userId": u.code})
    ontology_id = session.info.get("ontology_id")
    ontology_role = None
    member = None
    if ontology_id is not None:
        from app.modules.ontology.models import OntologyMember

        member = await session.scalar(
            select(OntologyMember).where(
                OntologyMember.ontology_id == ontology_id, OntologyMember.user_id == u.id
            )
        )
        ontology_role = member.role if member is not None else None
        if ontology_role not in ("owner", "editor", "viewer"):
            raise Forbidden("用户没有此本体工作空间的访问权限")
    permissions, roles = await function_access(session, u.id)
    return CurrentUser(
        id=u.code,
        name=u.name,
        func=permissions,
        roles=roles,
        row_filters=await object_row_access(session, u.id, member=member, user_code=u.code),
        is_ontology_admin=permissions.get("releases") == "manage",
        tenant_id=tenant_id,
        ontology_id=ontology_id,
        ontology_role=ontology_role,
    )


async def bind_workspace(session: AsyncSession, request: Request, user: CurrentUser) -> CurrentUser:
    """按 X-Ontology-Id（省略 = 空间 1）校验成员 / 密钥授权，绑定会话本体范围，返回带本体角色的身份。

    非法编号 422、不存在 404、无授权 403；校验通过前不改会话范围。
    """
    from app.common.ontology import DEFAULT_ONTOLOGY_ID, set_ontology_scope
    from app.modules.admin.models import SysUser
    from app.modules.admin.permissions import object_row_access
    from app.modules.ontology.models import OntologyMember, OntologyWorkspace

    raw = request.headers.get("x-ontology-id")
    if raw is not None and (
        len(raw) > 19 or not raw.isascii() or not raw.isdecimal() or int(raw) <= 0 or int(raw) > 2**63 - 1
    ):
        from app.core.errors import AppError

        raise AppError("本体工作空间编号无效", code="VALIDATION_FAILED", status_code=422)
    ontology_id = int(raw) if raw is not None else DEFAULT_ONTOLOGY_ID
    definition_only = definition_only_request(request)
    workspace = await session.get(OntologyWorkspace, ontology_id)
    if workspace is None or workspace.deleted_at is not None:
        from app.core.errors import NotFound

        raise NotFound("本体工作空间不存在")
    if user.ontology_ids is not None:
        # API key caller: user keys need membership, service-account keys an ontology role binding.
        from app.modules.admin.api_keys import key_row_filters, key_workspace_role

        role = await key_workspace_role(session, request, user, ontology_id)
        if role not in ("owner", "editor", "viewer", "service"):
            raise Forbidden("没有此本体工作空间的访问权限")
        if ontology_id not in user.ontology_ids:
            raise Forbidden("API 密钥没有此本体工作空间的权限")
        await set_ontology_scope(session, ontology_id)
        user = replace(
            user,
            ontology_id=ontology_id,
            ontology_role=role,
            row_filters=await key_row_filters(session, request, user),
        )
    else:
        membership = (
            await session.execute(
                select(OntologyMember, SysUser.code)
                .join(SysUser, SysUser.id == OntologyMember.user_id)
                .where(OntologyMember.ontology_id == ontology_id, SysUser.code == user.id)
            )
        ).one_or_none()
        member, user_code = membership if membership is not None else (None, None)
        if member is None or member.role not in ("owner", "editor", "viewer"):
            raise Forbidden("没有此本体工作空间的访问权限")
        await set_ontology_scope(session, ontology_id, load_data_bindings=not definition_only)
        user = replace(
            user,
            ontology_id=ontology_id,
            ontology_role=member.role,
            # No data access is granted to a definition-only request; unexpected row reads fail closed.
            row_filters={} if definition_only else await object_row_access(
                session, member.user_id, member=member, user_code=user_code
            ),
        )
    return user


async def get_current_user(
    session: SessionDep,
    request: Request,
    x_demo_as: Annotated[
        str | None, Header(description="仅显式启用的本地隔离 test 夹具使用；正常服务忽略")
    ] = None,
    x_tenant_id: Annotated[str | None, Header(description="一期单租户：只透传，不过滤")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    session.info["data_purpose"] = request.headers.get("X-Data-Purpose") or None
    cached = getattr(request.state, "current_user", None)
    if cached is not None:
        from app.modules.governance.query_guard import bind_request_context
        bind_request_context(session, cached, request)
        return cached
    if authorization is not None:
        from app.modules.admin.api_keys import authenticate_key

        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise Unauthorized("请使用 Bearer API 密钥")
        user = await authenticate_key(session, token, request, x_tenant_id)
    else:
        session.info.pop("api_caller", None)  # human caller: never inherit a machine caller context
        from app.modules.admin.login import fixture_identity_enabled, require_same_origin, session_user

        if fixture_identity_enabled():
            user = await resolve_user(session, x_demo_as or get_settings().dev_user, x_tenant_id)
        else:
            db_user = await session_user(session, request)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                require_same_origin(request)
            user = await _resolved_user(session, db_user, x_tenant_id)
    route = full_route_path(request)
    admin = is_admin_request(request)
    discovery = (
        admin
        or route.endswith("/workspaces")
        or ("/workspaces/" in route and not route.endswith("/workspaces/current"))
    )
    if admin:
        # 平台 admin 接口不依赖当前选中空间是否合法（ontology-workspaces.md）：空间可用时照常绑定，
        # 供按本体生效的角色行级范围 / 组织列 / 密钥签发使用；不可用时不报错、不绑定（需要本体上下文的接口
        # 自己返回 ONTOLOGY_CONTEXT_REQUIRED）。admin 写操作只按功能权限，不套用空间 viewer 只读。
        scoped_before = session.info.get("ontology_id")
        try:
            user = await bind_workspace(session, request, user)
        except AppError:
            if session.info.get("ontology_id") != scoped_before:
                raise  # 范围已绑定但后续失败：不能带着半绑定的会话继续
    elif not discovery:
        user = await bind_workspace(session, request, user)
        if user.ontology_role == "viewer" and request.method not in ("GET", "HEAD", "OPTIONS"):
            # History/preview exceptions also include writes; only these reviewed pure reads are allowed.
            pure_reads = {
                "/metrics/{id}:trial",
                "/knowledge:resolve",
                "/dimensions:match",
                "/action-types/{type_id}:dry-run",
                "/object-types/{code}/search",
                "/objects/{kind}/search",
                "/scoped-ask",
            }
            own_read_markers = {
                api_prefix("ontology") + path
                for path in ("/notifications/{notification_id}:read", "/notifications:read-all")
            }
            if request.method != "POST" or (
                route.rsplit("/v1", 1)[-1] not in pure_reads and route not in own_read_markers
            ):
                raise Forbidden("当前本体工作空间为只读权限")
    request.state.current_user = user
    from app.modules.governance.query_guard import bind_request_context
    bind_request_context(session, user, request)
    return user


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def api_key_guard(request: Request, session: SessionDep) -> None:
    """Apply the token allowlist even to API handlers without a CurrentUser dependency."""
    authorization = request.headers.get("authorization")
    if authorization is not None:
        await get_current_user(
            session, request, x_tenant_id=request.headers.get("x-tenant-id"), authorization=authorization
        )


ApiKeyGuardDep = Depends(api_key_guard)


async def ontology_guard(user: CurrentUserDep) -> None:
    """Resolve workspace authorization even for endpoints without a user parameter."""


OntologyGuardDep = Depends(ontology_guard)


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
