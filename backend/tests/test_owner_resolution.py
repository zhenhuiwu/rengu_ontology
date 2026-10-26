"""负责人只接受真实在用用户；存量姓名不允许歧义。"""

import pytest
from sqlalchemy import select

from app.core.errors import AppError
from app.modules.admin.models import SysUser, UserStatus
from app.modules.admin.permissions import resolve_owner_name


async def test_owner_code_and_unique_legacy_name(users):
    assert await resolve_owner_name(users, "U010001") == "王敏"
    assert await resolve_owner_name(users, "王敏") == "王敏"


@pytest.mark.parametrize("value", [None, [], "", "missing", "x" * 65, "x\x00", "\ud800"])
async def test_owner_invalid_is_business_error(users, value):
    with pytest.raises(AppError) as error:
        await resolve_owner_name(users, value)
    assert error.value.status_code == 422 and error.value.details["fields"][0]["field"] == "owner"


async def test_owner_disabled_ambiguous_and_code_precedence(users):
    first = await users.scalar(select(SysUser).where(SysUser.code == "U010001"))
    other = await users.scalar(select(SysUser).where(SysUser.code == "U010002"))
    other.name = first.name
    await users.flush()
    with pytest.raises(AppError, match="不唯一"):
        await resolve_owner_name(users, first.name)
    assert await resolve_owner_name(users, first.code) == first.name
    other.name = first.code
    first.status = UserStatus.disabled
    await users.flush()
    with pytest.raises(AppError, match="已停用"):
        await resolve_owner_name(users, first.code)
