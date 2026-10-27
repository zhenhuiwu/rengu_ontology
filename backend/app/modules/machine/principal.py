"""Executor principal for machine callers (Palantir).

服务账号没有 SysUser 行，也没有 runAs 用户字段（本体小白 ServiceAccount 模型现状）。为了让
`api:{服务账号编号}` 这个系统主体能穿过后台执行链（外部提交排队 / 副作用复核 / 动作事件发射），
这里把主体编码成稳定的 actor 文本，并在后台按服务账号 + 绑定角色重建身份，而不是要求在用真人。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, resolve_user

SERVICE_PREFIX = "api:"


def actor_of(user: CurrentUser) -> str:
    """Audit / created_by text of a caller: `api:{code}` for service accounts, user code otherwise."""
    return f"{SERVICE_PREFIX}{user.id}" if user.is_service_account else user.id


async def resolve_actor(session: AsyncSession, actor: str) -> CurrentUser:
    """Rebuild the executor from stored actor text; service principals keep their bound role only."""
    if not actor.startswith(SERVICE_PREFIX):
        return await resolve_user(session, actor)
    from app.modules.admin.api_keys import build_service_account_principal

    # 本体小白公开函数：未吊销 + 角色绑定当前本体空间（bind_ontology 默认 True），否则 401
    return await build_service_account_principal(session, actor[len(SERVICE_PREFIX):])
