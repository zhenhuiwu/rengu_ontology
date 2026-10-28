from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.common.auth import CurrentUserDep
from app.common.ontology import DEFAULT_ONTOLOGY_ID
from app.common.pagination import PageParams
from app.core.errors import Conflict, DuplicateKey, Forbidden, NotFound
from app.core.schema import ApiModel
from app.db.session import SessionDep
from app.modules.admin.models import SysRole, SysUser, UserStatus
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding, OntologyWorkspace

router = APIRouter(tags=["ontology-workspaces"])


class WorkspaceIn(ApiModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)
    domain: str = Field(default="", max_length=64)


def workspace_item(workspace: OntologyWorkspace, role: str) -> dict:
    return {
        "workersPaused": workspace.workers_paused,
        "id": workspace.id,
        "code": workspace.code,
        "name": workspace.name,
        "description": workspace.description,
        "domain": workspace.domain,
        "isDefault": workspace.is_default,
        "role": role,
    }


async def db_user_id(session, code: str) -> int:
    return await session.scalar(select(SysUser.id).where(SysUser.code == code))


async def workspace_directory(
    session,
    user,
    request: Request,
    page: int | None = None,
    size: int = 10,
) -> dict:
    query = (
        select(OntologyWorkspace, OntologyMember.role)
        .join(OntologyMember, OntologyMember.ontology_id == OntologyWorkspace.id)
        .where(OntologyMember.user_id == await db_user_id(session, user.id), OntologyWorkspace.deleted_at.is_(None))
        .order_by(OntologyWorkspace.id)
    )
    total = int(await session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    params = PageParams(page=page or 1, size=size).clamp(total)
    if page is not None:
        query = query.offset(params.offset).limit(params.size)
    rows = list(await session.execute(query))
    raw = request.headers.get("x-ontology-id")
    selected = (
        int(raw)
        if raw and raw.isascii() and raw.isdecimal() and len(raw) <= 19 and 0 < int(raw) < 2**63
        else DEFAULT_ONTOLOGY_ID
        if raw is None
        else None
    )
    has_selected = await session.scalar(
        select(OntologyMember.id).join(OntologyWorkspace, OntologyWorkspace.id == OntologyMember.ontology_id).where(
            OntologyMember.user_id == await db_user_id(session, user.id),
            OntologyMember.ontology_id == selected,
            OntologyWorkspace.deleted_at.is_(None),
        )
    )
    return {
        "items": [workspace_item(workspace, role) for workspace, role in rows],
        "total": total,
        "page": params.page,
        "size": params.size if page is not None else total,
        "currentOntologyId": selected if has_selected is not None else None,
        "canCreate": user.func.get("releases") == "manage",
    }


@router.get("/workspaces")
async def list_workspaces(
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 10,
) -> dict:
    return await workspace_directory(session, user, request, page, size)


@router.get("/workspaces/catalog")
async def workspace_catalog(session: SessionDep, user: CurrentUserDep, request: Request) -> dict:
    return await workspace_directory(session, user, request)


@router.get("/workspaces/deleted")
async def deleted_workspaces(session: SessionDep, user: CurrentUserDep) -> dict:
    if user.func.get("releases") != "manage" or user.ontology_ids is not None or user.is_service_account:
        raise Forbidden("只有工作空间所有者可以查看可恢复空间")
    rows = await session.execute(select(OntologyWorkspace).join(
        OntologyMember, OntologyMember.ontology_id == OntologyWorkspace.id
    ).where(OntologyMember.user_id == await db_user_id(session, user.id), OntologyMember.role == "owner",
            OntologyWorkspace.deleted_at.is_not(None)).order_by(OntologyWorkspace.id))
    return {"items": [{**workspace_item(row[0], "owner"), "deletedAt": row[0].deleted_at} for row in rows]}


@router.post("/workspaces", status_code=201)
async def create_workspace(body: WorkspaceIn, session: SessionDep, user: CurrentUserDep) -> dict:
    if user.func.get("releases") != "manage":
        raise Forbidden("只有平台本体管理员可以创建工作空间")
    if not body.name.strip():
        raise DuplicateKey("工作空间名称不能为空")
    workspace = OntologyWorkspace(**body.model_dump(), created_by=user.id)
    session.add(workspace)
    try:
        await session.flush()
        session.add(
            OntologyMember(ontology_id=workspace.id, user_id=await db_user_id(session, user.id), role="owner")
        )
        await session.flush()
        from app.modules.event.workspace_defaults import initialize_workspace_events

        await initialize_workspace_events(session, workspace.id, user.id)
    except IntegrityError as exc:
        raise DuplicateKey("工作空间编号已存在") from exc
    return workspace_item(workspace, "owner")


@router.get("/workspaces/current")
async def current_workspace(session: SessionDep, user: CurrentUserDep) -> dict:
    from app.modules.static.permissions import direct_publish_enabled

    workspace = await session.get(OntologyWorkspace, user.ontology_id)
    return {**workspace_item(workspace, user.ontology_role), "directPublishEnabled": direct_publish_enabled(user)}


class MemberIn(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    role: Literal["owner", "editor", "viewer"]


async def require_owner(session, ontology_id: int, user, *, include_deleted: bool = False) -> None:
    workspace = await session.get(OntologyWorkspace, ontology_id)
    if workspace is None or (workspace.deleted_at is not None and not include_deleted):
        raise NotFound("本体工作空间不存在")
    role = await session.scalar(
        select(OntologyMember.role).where(
            OntologyMember.ontology_id == ontology_id,
            OntologyMember.user_id == await db_user_id(session, user.id),
        )
    )
    if role != "owner" or user.func.get("releases") != "manage":
        raise Forbidden("只有本体工作空间所有者可以管理成员和对象权限绑定")


async def change_workspace_deleted(session, user, ontology_id: int, request: Request, *, restore: bool) -> dict:
    from app.common.ontology import set_ontology_scope
    from app.core import clock
    from app.modules.admin.repo import insert_audit

    if user.ontology_ids is not None or user.is_service_account:
        raise Forbidden("只有真实工作空间所有者可以删除或恢复")
    if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"ontology-lifecycle:{ontology_id}"}):
        raise Conflict("工作空间有正在执行的任务，请稍后重试", code="WORKSPACE_BUSY")
    workspace = await session.scalar(select(OntologyWorkspace).where(OntologyWorkspace.id == ontology_id).with_for_update())
    if workspace is None:
        raise NotFound("本体工作空间不存在")
    await require_owner(session, ontology_id, user, include_deleted=True)
    if workspace.is_default or workspace.id == DEFAULT_ONTOLOGY_ID:
        raise Forbidden("默认工作空间不能删除")
    if restore == (workspace.deleted_at is None):
        raise Conflict("工作空间状态已变化，请刷新后重试")
    if restore:
        workspace.deleted_at = None
        await session.flush()
    await set_ontology_scope(session, ontology_id)
    await insert_audit(session, actor=f"{user.name}（{user.id}）", action="恢复工作空间" if restore else "删除工作空间",
                       target=f"{workspace.code} ({workspace.id})", ip=request.client.host if request.client else "", created_by=user.id)
    if not restore:
        workspace.deleted_at = clock.now()
        workspace.workers_paused = True
    await session.flush()
    return {"id": workspace.id, "code": workspace.code, "name": workspace.name, "deleted": not restore, "workersPaused": workspace.workers_paused}


@router.post("/workspaces/{ontology_id}:delete")
async def delete_workspace(ontology_id: int, session: SessionDep, user: CurrentUserDep, request: Request) -> dict:
    return await change_workspace_deleted(session, user, ontology_id, request, restore=False)


@router.post("/workspaces/{ontology_id}:restore")
async def restore_workspace(ontology_id: int, session: SessionDep, user: CurrentUserDep, request: Request) -> dict:
    return await change_workspace_deleted(session, user, ontology_id, request, restore=True)


@router.post("/workspaces/{ontology_id}:resume-workers")
async def resume_workspace_workers(ontology_id: int, session: SessionDep, user: CurrentUserDep, request: Request) -> dict:
    from app.modules.admin.repo import insert_audit
    from app.common.ontology import set_ontology_scope

    if user.ontology_ids is not None or user.is_service_account:
        raise Forbidden("只有真实工作空间所有者可以启用后台任务")
    if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"ontology-lifecycle:{ontology_id}"}):
        raise Conflict("工作空间有正在执行的任务，请稍后重试", code="WORKSPACE_BUSY")
    workspace = await session.scalar(select(OntologyWorkspace).where(OntologyWorkspace.id == ontology_id).with_for_update())
    await require_owner(session, ontology_id, user)
    await set_ontology_scope(session, ontology_id)
    workspace.workers_paused = False
    await insert_audit(session, actor=f"{user.name}（{user.id}）", action="启用工作空间后台任务", target=f"{workspace.code} ({workspace.id})", ip=request.client.host if request.client else "", created_by=user.id)
    await session.flush()
    return {"id": workspace.id, "code": workspace.code, "name": workspace.name, "deleted": False, "workersPaused": False}


@router.get("/workspaces/{ontology_id}/members")
async def members(
    ontology_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 10,
) -> dict:
    await require_owner(session, ontology_id, user)
    query = (
        select(SysUser.code, SysUser.name, OntologyMember.role)
        .join(OntologyMember, OntologyMember.user_id == SysUser.id)
        .where(OntologyMember.ontology_id == ontology_id)
        .order_by(SysUser.code)
    )
    total = int(await session.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    params = PageParams(page=page, size=size).clamp(total)
    rows = await session.execute(query.offset(params.offset).limit(params.size))
    return {
        "items": [{"userId": code, "name": name, "role": role} for code, name, role in rows],
        "total": total,
        "page": params.page,
        "size": params.size,
    }


@router.get("/workspaces/{ontology_id}/member-options")
async def member_options(
    ontology_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    kw: Annotated[str, Query(max_length=128)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=200)] = 10,
) -> dict:
    await require_owner(session, ontology_id, user)
    query = select(SysUser.code, SysUser.name).where(SysUser.status == UserStatus.active)
    if kw.strip():
        query = query.where(
            or_(func.strpos(SysUser.code, kw.strip()) > 0, func.strpos(SysUser.name, kw.strip()) > 0)
        )
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    params = PageParams(page=page, size=size).clamp(total)
    rows = await session.execute(query.order_by(SysUser.code).offset(params.offset).limit(params.size))
    return {
        "items": [{"id": code, "name": name} for code, name in rows],
        "total": total,
        "page": params.page,
        "size": params.size,
    }


@router.put("/workspaces/{ontology_id}/members")
async def save_member(ontology_id: int, body: MemberIn, session: SessionDep, user: CurrentUserDep) -> dict:
    await require_owner(session, ontology_id, user)
    target = await session.scalar(
        select(SysUser).where(SysUser.code == body.user_id, SysUser.status == UserStatus.active)
    )
    if target is None:
        raise NotFound("成员用户不存在或已停用")
    # An owner may add another owner but cannot remove the last owner through self-demotion.
    if body.user_id == user.id and body.role != "owner":
        raise Forbidden("所有者不能在此操作中降低自己的权限")
    member = await session.scalar(
        select(OntologyMember).where(
            OntologyMember.ontology_id == ontology_id, OntologyMember.user_id == target.id
        )
    )
    if member is None:
        session.add(OntologyMember(ontology_id=ontology_id, user_id=target.id, role=body.role))
    else:
        member.role = body.role
    await session.flush()
    return {"userId": target.code, "name": target.name, "role": body.role}


class RoleBindingsIn(ApiModel):
    role_codes: list[str] = Field(max_length=200)


@router.get("/workspaces/{ontology_id}/role-bindings")
async def object_role_bindings(ontology_id: int, session: SessionDep, user: CurrentUserDep) -> dict:
    await require_owner(session, ontology_id, user)
    roles = list(await session.scalars(select(SysRole).order_by(SysRole.code)))
    bound = set(
        await session.scalars(
            select(OntologyRoleBinding.role_id).where(OntologyRoleBinding.ontology_id == ontology_id)
        )
    )
    return {
        "roleCodes": [role.code for role in roles if role.id in bound],
        "items": [{"code": role.code, "name": role.name, "description": role.description} for role in roles],
    }


@router.put("/workspaces/{ontology_id}/role-bindings")
async def bind_object_roles(
    ontology_id: int, body: RoleBindingsIn, session: SessionDep, user: CurrentUserDep
) -> dict:
    await require_owner(session, ontology_id, user)
    roles = list(await session.scalars(select(SysRole).where(SysRole.code.in_(set(body.role_codes)))))
    if len(roles) != len(set(body.role_codes)):
        raise NotFound("角色不存在")
    await session.execute(delete(OntologyRoleBinding).where(OntologyRoleBinding.ontology_id == ontology_id))
    session.add_all([OntologyRoleBinding(ontology_id=ontology_id, role_id=role.id) for role in roles])
    await session.flush()
    return {"roleCodes": sorted(role.code for role in roles)}
