"""数据库角色的功能权限，只聚合实际成员关联，不按姓名或配置补授权。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.models import SysRole, SysRoleMember, SysRoleScope


async def function_access(session: AsyncSession, user_id: int) -> tuple[dict[str, str], tuple[str, ...]]:
    rows = await session.execute(
        select(SysRole.code, SysRole.func)
        .join(SysRoleMember, SysRoleMember.role_id == SysRole.id)
        .where(SysRoleMember.user_id == user_id)
        .order_by(SysRole.code)
    )
    merged: dict[str, str] = {}
    roles: list[str] = []
    rank = {"none": 0, "view": 1, "manage": 2}
    for code, permissions in rows:
        roles.append(code)
        if not isinstance(permissions, dict):
            continue
        for page, level in permissions.items():
            if isinstance(level, str) and rank.get(level, 0) > rank.get(merged.get(page, "none"), 0):
                merged[page] = level
    return merged, tuple(roles)


async def object_row_access(session: AsyncSession, user_id: int) -> dict[str, dict | None]:
    """Batch current role grants and scopes; missing/ambiguous identities never grant data access."""
    from app.common.sql_guard import RoleAccess, effective_expr
    from app.modules.static.models import ObjectStatus, ObjectType

    roles = list(
        await session.execute(
            select(SysRole.id, SysRole.objs, SysRole.default_deny)
            .join(SysRoleMember, SysRoleMember.role_id == SysRole.id)
            .where(SysRoleMember.user_id == user_id)
            .order_by(SysRole.code)
        )
    )
    if not roles:
        return {}
    scopes = {role.id: [] for role in roles}
    for scope in await session.execute(
        select(
            SysRoleScope.role_id,
            SysRoleScope.obj_type,
            SysRoleScope.prop,
            SysRoleScope.op,
            SysRoleScope.values,
        )
        .where(SysRoleScope.role_id.in_(scopes))
        .order_by(SysRoleScope.role_id, SysRoleScope.seq, SysRoleScope.id)
    ):
        scopes[scope.role_id].append(scope)
    objects = list(
        await session.execute(
            select(ObjectType.code, ObjectType.api_name, ObjectType.display_name).where(
                ObjectType.status == ObjectStatus.published
            )
        )
    )
    identities = {obj.api_name: {obj.code, obj.api_name, obj.display_name} for obj in objects}
    output = {}
    for obj in objects:
        access = []
        for role in roles:
            permissions = [p for p in role.objs if isinstance(p, dict)] if isinstance(role.objs, list) else []
            by_label = {}
            for entry in permissions:
                api = entry.get("api")
                if not isinstance(api, str) or api not in identities:
                    continue
                labels = set(identities[api])
                if isinstance(entry.get("objType"), str):
                    labels.add(entry["objType"])
                for label in labels:
                    by_label.setdefault(label, set()).add(api)
            unmapped = any(len(by_label.get(scope.obj_type, ())) != 1 for scope in scopes[role.id])
            matches = [p for p in permissions if p.get("api") == obj.api_name]
            if len(matches) != 1 or matches[0].get("view") is not True:
                continue
            permission = matches[0]
            names = {obj.code, obj.api_name, obj.display_name}
            if isinstance(permission.get("objType"), str):
                names.add(permission["objType"])
            scoped = []
            for scope in scopes[role.id]:
                if scope.obj_type not in names:
                    continue
                # Duplicated labels must not transfer another object's scope or become unlimited.
                ambiguous = len(by_label.get(scope.obj_type, ())) != 1
                scoped.append(
                    {"prop": "" if ambiguous else scope.prop, "op": scope.op, "values": scope.values}
                )
            if not scoped and unmapped:
                scoped.append({"prop": "", "op": "=", "values": []})
            access.append(RoleAccess(view=True, default_deny=role.default_deny, scopes=tuple(scoped)))
        output[obj.code] = effective_expr(obj.code, access)
    return output
