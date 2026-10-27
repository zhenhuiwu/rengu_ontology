"""数据库角色的功能权限，只聚合实际成员关联，不按姓名或配置补授权。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.admin.models import SysRole, SysRoleMember, SysRoleScope, SysUser, UserStatus


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
    from app.modules.ontology.models import OntologyMember, OntologyRoleBinding

    ontology_id = session.info.get("ontology_id")
    if ontology_id is None:
        return {}
    member = await session.scalar(
        select(OntologyMember.id).where(
            OntologyMember.ontology_id == ontology_id, OntologyMember.user_id == user_id
        )
    )
    if member is None:
        return {}

    roles = list(
        await session.execute(
            select(SysRole.id, SysRole.objs, SysRole.default_deny)
            .join(SysRoleMember, SysRoleMember.role_id == SysRole.id)
            .join(OntologyRoleBinding, OntologyRoleBinding.role_id == SysRole.id)
            .where(SysRoleMember.user_id == user_id, OntologyRoleBinding.ontology_id == ontology_id)
            .order_by(SysRole.code)
        )
    )
    return await role_row_access(session, roles)


async def role_row_access(session: AsyncSession, roles: list) -> dict[str, dict | None]:
    """Row filters for already ontology-bound roles (rows with id / objs / default_deny).

    Shared by user identities and service accounts. Scopes are read for the current ontology only
    (explicit predicate + FORCE RLS). A scope with object_type_id matches exactly that object type;
    a scope whose referenced object type was deleted (code kept, id nulled) is unmapped and denies;
    legacy rows without either keep the label matching, where ambiguity is also deny.
    """
    from app.common.sql_guard import RoleAccess, effective_expr
    from app.modules.static.models import ObjectStatus, ObjectType

    ontology_id = session.info.get("ontology_id")
    if ontology_id is None or not roles:
        return {}
    scopes = {role.id: [] for role in roles}
    for scope in await session.execute(
        select(
            SysRoleScope.role_id,
            SysRoleScope.obj_type,
            SysRoleScope.object_type_id,
            SysRoleScope.object_type_code,
            SysRoleScope.prop,
            SysRoleScope.op,
            SysRoleScope.values,
        )
        .where(SysRoleScope.role_id.in_(scopes), SysRoleScope.ontology_id == ontology_id)
        .order_by(SysRoleScope.role_id, SysRoleScope.seq, SysRoleScope.id)
    ):
        scopes[scope.role_id].append(scope)
    objects = list(
        await session.execute(
            select(ObjectType.id, ObjectType.code, ObjectType.api_name, ObjectType.display_name).where(
                ObjectType.status == ObjectStatus.published
            )
        )
    )
    identities = {obj.api_name: {obj.code, obj.api_name, obj.display_name} for obj in objects}
    published_ids = {obj.id for obj in objects}
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

            def unmapped_scope(scope, by_label=by_label) -> bool:
                if scope.object_type_id is not None:
                    return scope.object_type_id not in published_ids
                if scope.object_type_code is not None:
                    return True
                return len(by_label.get(scope.obj_type, ())) != 1

            unmapped = any(unmapped_scope(scope) for scope in scopes[role.id])
            matches = [p for p in permissions if p.get("api") == obj.api_name]
            if len(matches) != 1 or matches[0].get("view") is not True:
                continue
            permission = matches[0]
            names = {obj.code, obj.api_name, obj.display_name}
            if isinstance(permission.get("objType"), str):
                names.add(permission["objType"])
            scoped = []
            for scope in scopes[role.id]:
                if scope.object_type_id is not None or scope.object_type_code is not None:
                    if scope.object_type_id != obj.id:
                        continue
                    scoped.append({"prop": scope.prop, "op": scope.op, "values": scope.values})
                    continue
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


async def active_user_names(session: AsyncSession) -> list[dict[str, str]]:
    """构建负责人小目录；不返回账号、部门或角色信息。调用者检查对应构建页面管理权。"""
    rows = await session.execute(
        select(SysUser.code, SysUser.name).where(SysUser.status == UserStatus.active).order_by(SysUser.code)
    )
    return [{"id": code, "name": name} for code, name in rows]


async def users_with_function_access(session: AsyncSession, required: dict[str, str]) -> list[dict[str, str]]:
    """批量合并启用用户的真实角色，返回满足全部页面要求的小目录。"""
    rows = await session.execute(
        select(SysUser.code, SysUser.name, SysRole.func)
        .join(SysRoleMember, SysRoleMember.user_id == SysUser.id)
        .join(SysRole, SysRole.id == SysRoleMember.role_id)
        .where(SysUser.status == UserStatus.active)
        .order_by(SysUser.code)
    )
    rank = {"none": 0, "view": 1, "manage": 2}
    users: dict[str, tuple[str, dict[str, int]]] = {}
    for code, name, permissions in rows:
        _, access = users.setdefault(code, (name, {}))
        if isinstance(permissions, dict):
            for page, level in permissions.items():
                if isinstance(level, str):
                    access[page] = max(access.get(page, 0), rank.get(level, 0))
    return [
        {"id": code, "name": name}
        for code, (name, access) in users.items()
        if all(access.get(page, 0) >= rank[level] for page, level in required.items())
    ]


async def resolve_owner_name(session: AsyncSession, code_or_name: str) -> str:
    """新表单传用户编号；存量唯一姓名兼容。必须在调用方写事务里再校验一次。"""

    def invalid(message: str) -> AppError:
        return AppError(
            message,
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": "owner", "message": message}]},
        )

    if not isinstance(code_or_name, str) or not code_or_name.strip() or len(code_or_name) > 64:
        raise invalid("请选择有效负责人")
    value = code_or_name.strip()
    if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise invalid("负责人包含无效字符")
    user = await session.scalar(select(SysUser).where(SysUser.code == value))
    if user is None:
        matches = list(await session.scalars(select(SysUser).where(SysUser.name == value)))
        if len(matches) > 1:
            raise invalid("负责人姓名不唯一，请使用用户编号重新选择")
        if not matches:
            raise invalid("负责人不存在，请重新选择")
        user = matches[0]
    if user.status != UserStatus.active:
        raise invalid("负责人已停用，请重新选择")
    return user.name
