"""角色成员只接受启用用户，整批预校验不得留下部分成员或审计。"""

import pytest
from sqlalchemy import func, select

from app.modules.admin.models import AuditLog, SysRole, SysRoleMember, SysUser, UserStatus
from tests.test_admin_roles import role_actor  # noqa: F401


@pytest.mark.parametrize("status", [UserStatus.disabled, UserStatus.pending])
async def test_member_batch_rejects_inactive_without_partial_writes(client, users, status):
    active = SysUser(code="U-MEM-A", name="启用成员", role="系统管理员", status=UserStatus.active)
    inactive = SysUser(code="U-MEM-I", name="非启用成员", role="系统管理员", status=status)
    users.add_all([active, inactive])
    await users.flush()
    role = await users.scalar(select(SysRole).where(SysRole.code == "R-04"))
    before_members = await users.scalar(select(func.count()).select_from(SysRoleMember))
    before_audit = await users.scalar(select(func.count()).select_from(AuditLog))
    response = await client.post(
        "/api/admin/v1/roles/R-04/members", json={"userIds": [active.code, inactive.code]}
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert response.json()["details"]["ids"] == ["U-MEM-I"]
    assert await users.scalar(select(func.count()).select_from(SysRoleMember)) == before_members
    assert await users.scalar(select(func.count()).select_from(AuditLog)) == before_audit
    response = await client.post(
        "/api/admin/v1/roles/R-04/members", json={"userIds": [active.code, active.code]}
    )
    assert response.status_code == 200 and response.json() == {"added": 1}
    assert (
        await users.scalar(
            select(func.count())
            .select_from(SysRoleMember)
            .where(SysRoleMember.role_id == role.id, SysRoleMember.user_id == active.id)
        )
        == 1
    )
    assert await users.scalar(select(func.count()).select_from(AuditLog)) == before_audit + 1
    # 已有成员幂等跳过，不因其之后停用而把重复添加当作新授权。
    active.status = UserStatus.disabled
    await users.flush()
    response = await client.post("/api/admin/v1/roles/R-04/members", json={"userIds": [active.code]})
    assert response.status_code == 200 and response.json() == {"added": 0}
    assert await users.scalar(select(func.count()).select_from(AuditLog)) == before_audit + 1
