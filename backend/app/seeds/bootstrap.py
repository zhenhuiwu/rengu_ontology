"""Minimal first-install system records; never import retail definitions or reset users."""

import asyncio

from sqlalchemy import func, select, text

from app.core import clock
from app.modules.admin.models import (
    AuthCredential,
    SysOrg,
    SysRole,
    SysRoleMember,
    SysTenant,
    SysUser,
    UserStatus,
)
from app.modules.admin.roles import func_pages
from app.modules.event.models import EventType
from app.modules.event.workspace_defaults import STANDARD_EVENTS, initialize_workspace_events
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding, OntologyWorkspace

CREATOR = "system-init"
ADMIN_CODE = "U010001"
ROLE_CODE = "SYSTEM-ADMIN"


async def initialize_system(session, *, apply: bool = False) -> dict:
    """Default inspection is read only. Existing installations must use normal administration."""
    if session.info.get("ontology_id") is not None:
        raise ValueError("系统初始化需要独立的无工作空间会话")
    if not apply:
        await session.execute(text("SET TRANSACTION READ ONLY"))
    else:
        await session.execute(
            text("LOCK TABLE sys_user, sys_role, sys_tenant, sys_org IN SHARE ROW EXCLUSIVE MODE")
        )
    users = list(await session.scalars(select(SysUser)))
    admin = next((u for u in users if u.code == ADMIN_CODE), None)
    # Never turn an existing identity into an administrator or reset its password.
    if users and (
        len(users) != 1
        or admin is None
        or admin.created_by != CREATOR
        or admin.account != "admin"
        or admin.status != UserStatus.active
    ):
        raise ValueError("既有用户不是本入口创建的初始管理员；请通过正常管理流程处理，未覆盖")
    role = await session.scalar(select(SysRole).where(SysRole.code == ROLE_CODE))
    if role is not None and role.created_by != CREATOR:
        raise ValueError("系统管理员角色编号已被占用；未覆盖")
    workspace = await session.get(OntologyWorkspace, 1)
    if workspace is None or workspace.deleted_at is not None:
        raise ValueError("需要迁移建立的有效工作空间 1；未自动创建或恢复工作空间")
    # Explicit RLS scope for inspection only; the shared ORM session keeps its identity boundary.
    scope = text("SELECT set_config('rengu.ontology_id', :id, true)")
    await session.execute(scope, {"id": "1"})
    codes = set(await session.scalars(select(EventType.code)))
    occupied = await session.scalar(
        text("""
        SELECT EXISTS (SELECT 1 FROM object_type)
            OR EXISTS (SELECT 1 FROM action_type) OR EXISTS (SELECT 1 FROM rule)
            OR EXISTS (SELECT 1 FROM ontology_release) OR EXISTS (SELECT 1 FROM event)
    """)
    )
    await session.execute(scope, {"id": ""})
    expected = {code for code, _, _ in STANDARD_EVENTS}
    if codes - expected or occupied:
        raise ValueError("工作空间 1 已含业务定义或运行数据；需要正常管理流程，未覆盖")
    tenant_missing = not await session.scalar(select(func.count()).select_from(SysTenant))
    org_missing = not await session.scalar(select(func.count()).select_from(SysOrg))
    plan = {
        "mode": "apply" if apply else "dry-run",
        "ontologyId": 1,
        "createAdmin": admin is None,
        "createRole": role is None,
        "createTenant": tenant_missing,
        "createOrganization": org_missing,
        "ensureAdminMemberships": True,
        "createCredentialOnlyIfMissing": True,
        "createStandardEvents": sorted(expected - codes),
        "demoRows": 0,
    }
    if not apply:
        return plan
    if admin is None:
        admin = SysUser(
            code=ADMIN_CODE,
            account="admin",
            name="admin",
            dept="仁谷科技",
            status=UserStatus.active,
            created_by=CREATOR,
        )
        session.add(admin)
        await session.flush()
    if await session.get(AuthCredential, admin.id) is None:
        from app.modules.admin.login import hash_password

        session.add(
            AuthCredential(user_id=admin.id, password_hash=await asyncio.to_thread(hash_password, "admin123"))
        )
    if role is None:
        role = SysRole(
            code=ROLE_CODE,
            name="系统管理员",
            built_in=True,
            default_deny=False,
            func={p["key"]: "manage" for p in func_pages()},
            objs=[],
            changed_at=clock.now(),
            changed_by="admin",
            created_by=CREATOR,
        )
        session.add(role)
        await session.flush()
    for model, conditions, values in (
        (
            SysRoleMember,
            (SysRoleMember.role_id == role.id, SysRoleMember.user_id == admin.id),
            {"role_id": role.id, "user_id": admin.id, "seq": 0},
        ),
        (
            OntologyMember,
            (OntologyMember.ontology_id == 1, OntologyMember.user_id == admin.id),
            {"ontology_id": 1, "user_id": admin.id, "role": "owner"},
        ),
        (
            OntologyRoleBinding,
            (OntologyRoleBinding.ontology_id == 1, OntologyRoleBinding.role_id == role.id),
            {"ontology_id": 1, "role_id": role.id},
        ),
    ):
        if await session.scalar(select(model.id).where(*conditions)) is None:
            session.add(model(**values))
    if tenant_missing:
        session.add(
            SysTenant(code="TN-XL-000317", name="仁谷科技", short_name="仁谷科技", created_by=CREATOR)
        )
    if org_missing:
        session.add(SysOrg(code="hd", name="仁谷科技", seq=0, is_default=True, created_by=CREATOR))
    if expected - codes:
        await initialize_workspace_events(session, 1, ADMIN_CODE, codes=expected - codes)
    await session.flush()
    return plan
