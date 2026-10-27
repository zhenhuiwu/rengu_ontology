"""Unexecuted status-guard checks. No database, HTTP, real users, or permission changes."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.admin import service
from app.modules.admin.models import SysUser, UserStatus


async def main():
    users = service.UserService(object())
    actor = CurrentUser(id="ACTOR", name="Actor")
    target = SysUser(id=2, code="TARGET", name="Target", status=UserStatus.active)

    async def rejected(status, expected, *, acting=actor, transfer_to=None):
        try:
            await users._check_status_change(target, status, acting, transfer_to)
        except AppError as error:
            assert error.code == expected
        else:
            raise AssertionError("An unsafe status change was accepted")

    with (
        patch.object(service, "active_admin_ids", AsyncMock(return_value={1, 2})),
        patch.object(service, "pending_approvals", AsyncMock(return_value={"TARGET": 3})) as pending,
    ):
        await users._check_status_change(target, UserStatus.active, actor)
        pending.assert_not_awaited()  # Ordinary profile edits do not trigger deactivation checks.
        await rejected(UserStatus.disabled, "NO_PERMISSION", acting=CurrentUser(id="TARGET", name="Target"))
        await rejected(UserStatus.pending, "NO_PERMISSION", acting=CurrentUser(id="TARGET", name="Target"))
        await rejected(UserStatus.disabled, "TRANSFER_REQUIRED")
        await rejected(UserStatus.pending, "TRANSFER_REQUIRED")
        await rejected(UserStatus.disabled, "TRANSFER_UNAVAILABLE", transfer_to="OTHER")
    with patch.object(service, "active_admin_ids", AsyncMock(return_value={2})):
        await rejected(UserStatus.disabled, "STATE_INVALID")
        await rejected(UserStatus.pending, "STATE_INVALID")
    with (
        patch.object(service, "active_admin_ids", AsyncMock(return_value={1, 2})),
        patch.object(service, "pending_approvals", AsyncMock(side_effect=RuntimeError("source unavailable"))),
    ):
        try:
            await users._check_status_change(target, UserStatus.disabled, actor)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Unavailable approval sources must fail closed")


if __name__ == "__main__":
    asyncio.run(main())
