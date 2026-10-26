"""仅测试夹具：明确写入角色成员，不替换身份解析/权限检查。"""

from sqlalchemy import func, select

from app.modules.admin.models import SysRole, SysRoleMember, SysUser


async def grant_fixture_roles(session, assignments=None):
    if assignments is None:
        assignments = {"U010001": "R-01", "U010002": "R-02", "U010004": "R-02"}
    for code, role_code in assignments.items():
        user_id = await session.scalar(select(SysUser.id).where(SysUser.code == code))
        role_id = await session.scalar(select(SysRole.id).where(SysRole.code == role_code))
        assert user_id is not None and role_id is not None
        exists = await session.scalar(
            select(SysRoleMember.id).where(
                SysRoleMember.role_id == role_id,
                SysRoleMember.user_id == user_id,
            )
        )
        if exists is None:
            seq = await session.scalar(
                select(func.min(SysRoleMember.seq)).where(SysRoleMember.role_id == role_id)
            )
            session.add(SysRoleMember(role_id=role_id, user_id=user_id, seq=(seq or 0) - 1))
    await session.flush()
