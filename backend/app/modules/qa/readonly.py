"""qa 原生只读连接的本体收窄：SET LOCAL ROLE rengu_ontology_scoped + rengu.ontology_id。

ORM 会话由 app/common/ontology 的 after_begin 监听统一降权；qa 自己开的只读连接（问数执行、
评测任务、对象外部源）不走 ORM 会话，在这里补同样的两步。助手放在 qa 模块内（不改公共模块）。

规则：
- 连接账号是 rengu_ontology_scoped 的成员 → SET LOCAL ROLE（事务结束自动复原），RLS 按本体生效；
- 不是成员（外部库没有这个角色，或运维没授）且账号是超级用户 / BYPASSRLS → 拒绝（503），
  否则 RLS 形同虚设；
- 不是成员的普通账号 → 照旧只设 rengu.ontology_id（RLS 策略按它过滤）。
必须在事务里调用（SET LOCAL / set_config(..., true) 只在当前事务有效）。
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.common.ontology import RUNTIME_ROLE
from app.core.errors import AppError


class QaScopeUnavailable(AppError):
    status_code = 503
    code = "QA_EXEC_UNAVAILABLE"
    message = "问数只读账号不能是超级用户或绕过行权限，且未授予本体收窄角色"


_PROBE = text(
    "SELECT r.oid IS NOT NULL AS role_exists, "
    "COALESCE(pg_has_role(current_user, r.oid, 'MEMBER'), false) AS is_member, "
    "u.rolsuper, u.rolbypassrls "
    "FROM pg_roles u LEFT JOIN pg_roles r ON r.rolname = :role "
    "WHERE u.rolname = current_user"
)


async def scope_readonly_conn(
    conn: AsyncConnection,
    ontology_id: int,
    *,
    error: Callable[[], Exception] = QaScopeUnavailable,
) -> bool:
    """在 conn 当前事务里收窄到本体。返回是否切换了角色。"""
    if type(ontology_id) is not int or ontology_id <= 0:
        raise AppError("本体工作空间编号无效", code="VALIDATION_FAILED", status_code=422)
    row = (await conn.execute(_PROBE, {"role": RUNTIME_ROLE})).mappings().one()
    switched = bool(row["is_member"])
    if switched:
        await conn.exec_driver_sql(f"SET LOCAL ROLE {RUNTIME_ROLE}")
    elif row["rolsuper"] or row["rolbypassrls"]:
        raise error()
    await conn.execute(
        text("SELECT set_config('rengu.ontology_id', :ontology_id, true)"),
        {"ontology_id": str(ontology_id)},
    )
    return switched
