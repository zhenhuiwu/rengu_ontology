"""Executor principal for machine callers (Palantir).

服务账号没有 SysUser 行，也没有 runAs 用户字段（本体小白 ServiceAccount 模型现状）。为了让
`api:{服务账号编号}` 这个系统主体能穿过后台执行链（外部提交排队 / 副作用复核 / 动作事件发射），
这里把主体编码成稳定的 actor 文本，并在后台按服务账号 + 绑定角色重建身份，而不是要求在用真人。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, resolve_user
from app.core.errors import Unauthorized

SERVICE_PREFIX = "api:"


def actor_of(user: CurrentUser) -> str:
    """Audit / created_by text of a caller: `api:{code}` for service accounts, user code otherwise."""
    return f"{SERVICE_PREFIX}{user.id}" if user.is_service_account else user.id


async def resolve_actor(session: AsyncSession, actor: str) -> CurrentUser:
    """Rebuild the executor from stored actor text; service principals keep their bound role only."""
    if not actor.startswith(SERVICE_PREFIX):
        return await resolve_user(session, actor)
    from app.modules.admin.api_keys import role_bound
    from app.modules.admin.models import ServiceAccount, SysRole
    from app.modules.admin.permissions import role_row_access

    code = actor[len(SERVICE_PREFIX):]
    account = await session.scalar(select(ServiceAccount).where(ServiceAccount.code == code))
    if account is None or account.revoked_at is not None:
        raise Unauthorized("服务账号已吊销或不存在", details={"principal": actor})
    role = await session.get(SysRole, account.role_id)
    ontology_id = session.info.get("ontology_id")
    if role is None or ontology_id is None or not await role_bound(session, role.id, ontology_id):
        raise Unauthorized("服务账号角色未绑定此本体工作空间", details={"principal": actor})
    return CurrentUser(
        id=account.code,
        name=account.name,
        func=role.func if isinstance(role.func, dict) else {},
        roles=(role.code,),
        row_filters=await role_row_access(session, [role]),
        is_service_account=True,
        ontology_id=ontology_id,
    )
