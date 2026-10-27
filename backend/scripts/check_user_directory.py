"""Optional local self-check using transient mocks only; no database, HTTP, users or grants.

Not executed during delivery because backend tests are prohibited.
Run later from backend with PYTHONPATH=. python scripts/check_user_directory.py.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.admin.roles import sync_primary_role
from app.modules.admin.models import UserStatus
from app.modules.admin.schemas import UserUpdateIn, department_name
from app.modules.admin.service import UserService, department_options


def session_with(members):
    return SimpleNamespace(execute=AsyncMock(return_value=members), scalar=AsyncMock(return_value=0),
                           delete=AsyncMock(), add=Mock())


async def main():
    restricted = CurrentUser(id="operator", name="Operator", func={"users": "manage"})
    service = UserService(SimpleNamespace())
    try:
        await service._new(name="N", account="n@example.com", dept="D", role="R", status=UserStatus.pending, actor=restricted)
    except AppError as error:
        assert error.status_code == 403
    else:
        raise AssertionError("Role assignment did not require roles:manage")
    service.preview = AsyncMock(return_value=[])
    try:
        await service.import_rows([], restricted)
    except AppError as error:
        assert error.status_code == 403
    else:
        raise AssertionError("Import did not require roles:manage")
    service.preview.assert_not_awaited()
    existing = SimpleNamespace(id=99, role="reader")
    session = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(id=2, name="modeler")), flush=AsyncMock())
    service = UserService(session)
    service._get = AsyncMock(return_value=existing)
    service.repo.update = AsyncMock(return_value=existing)
    try:
        await service.update("U", UserUpdateIn(name="N", dept="D", role="modeler", status=UserStatus.active), restricted)
    except AppError as error:
        assert error.status_code == 403
    else:
        raise AssertionError("Primary role change did not require roles:manage")
    service.repo.update.assert_not_awaited()
    session.scalar.return_value = SimpleNamespace(id=1, name="reader")
    await service.update("U", UserUpdateIn(name="N", dept="D", role="reader", status=UserStatus.active), restricted)
    service.repo.update.assert_awaited_once()  # Ordinary profile edits remain available to user managers.
    old, extra = SimpleNamespace(role_id=1), SimpleNamespace(role_id=3)
    selected = SimpleNamespace(id=2, name="modeler")
    session = session_with([(old, "reader"), (extra, "approver")])
    await sync_primary_role(session, 99, "reader", selected, "operator")
    assert [call.args[0] for call in session.delete.call_args_list] == [old]
    added = session.add.call_args.args[0]
    assert (added.role_id, added.user_id) == (2, 99)
    session = session_with([(old, "reader"), (extra, "approver")])
    await sync_primary_role(session, 99, "reader", SimpleNamespace(id=3, name="approver"), "operator")
    assert [call.args[0] for call in session.delete.call_args_list] == [old]
    session.add.assert_not_called()
    session = session_with([])
    await sync_primary_role(session, 99, "reader", SimpleNamespace(id=1, name="reader"), "operator")
    session.execute.assert_not_awaited()  # Editing a name must not restore a separately removed grant.
    session = session_with([])
    await sync_primary_role(session, 99, None, selected, "operator")
    assert session.add.call_args.args[0].role_id == selected.id
    directory = SimpleNamespace(scalars=AsyncMock(side_effect=[["设备运维"], ["设备运维", "检修组"]]))
    assert await department_options(directory) == ["设备运维", "检修组"]
    assert department_name("  设备运维 / 检修组  ") == "设备运维 / 检修组"
    assert len(department_name("部" * 128)) == 128
    for value in ("", "   ", "部" * 129, "检\x00修", "检\n修", "检\ud800修"):
        try:
            department_name(value)
        except ValueError:
            continue
        raise AssertionError("Invalid department accepted")


if __name__ == "__main__":
    asyncio.run(main())
