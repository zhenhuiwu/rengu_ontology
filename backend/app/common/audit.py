"""审计（EXTENSION POINT (PDE)）。

业务模块在“写操作成功”或“被拒绝”时调用：

    await audit(session, user, action="ontology.release.publish", target="v2026.10.4", result="成功")

签名定死，PDE 实现时写入只追加的 sys_audit_log（与业务写入同一事务），并支持 cursor 分页查询。
现在的桩实现只打日志。
"""

import logging
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser

AuditResult = Literal["成功", "失败", "拒绝"]
_log = logging.getLogger("app.audit")


async def audit(
    session: AsyncSession,
    user: CurrentUser | None,
    *,
    action: str,
    target: str,
    result: AuditResult = "成功",
    detail: dict[str, Any] | None = None,
) -> None:
    # EXTENSION POINT (PDE): 写 sys_audit_log（actor、ip、预览身份、前后值……）
    _log.info("audit actor=%s action=%s target=%s result=%s", user and user.name, action, target, result)
