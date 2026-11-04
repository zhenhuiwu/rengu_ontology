"""Pure account-profile check; no database, HTTP or approval scanning."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.common.auth import CurrentUser
from app.modules.admin.models import SysUser, UserStatus
from app.modules.admin.router import current_profile


async def main():
    account = SysUser(code="U010001", name="admin", account="admin", dept="仁谷科技",
                      role="管理员", status=UserStatus.active, joined_on=date(2026, 11, 4), lock_version=1)
    session = SimpleNamespace(scalars=AsyncMock(return_value=[]), scalar=AsyncMock(return_value=account))
    actor = CurrentUser(id="U010001", name="admin")
    with patch("app.modules.admin.router.pending_approvals", side_effect=AssertionError("must not scan approvals")):
        basic = await current_profile(session, actor)
        assert "accountDetails" not in basic and session.scalar.await_count == 0
        detailed = await current_profile(session, actor, include_account=True)
        assert detailed["accountDetails"]["account"] == "admin"
        assert set(detailed["accountDetails"]) == {"id", "name", "account", "dept", "status", "lastLogin", "createdAt"}
        assert "U010001" in str(session.scalar.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        session.scalar.return_value = None
        assert (await current_profile(session, actor, include_account=True))["accountDetails"] is None
    print("Account profile passed: current identity only, optional pure details, no approval scan.")


if __name__ == "__main__":
    asyncio.run(main())
