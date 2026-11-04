"""Explicit, versioned configuration. Editing policy never grants legacy object/page rights."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, text

from app.common.auth import CurrentUserDep, require_func
from app.common.ontology import require_ontology_id
from app.core.errors import AppError, Forbidden, VersionConflict
from app.db.session import SessionDep
from app.modules.admin.api_keys import require_api_scope
from app.modules.admin.repo import insert_audit
from app.modules.governance.models import GovernancePolicy
from app.modules.governance.policy import (
    decide,
    effective_policies,
    load_definition,
    policy_graph,
    role_catalog,
)
from app.modules.governance.schemas import PolicySave

router = APIRouter(prefix="/governance", tags=["governance"])
Manager = Annotated[object, Depends(require_func("roles", "manage"))]


async def _ready(session):
    if not await session.scalar(text("SELECT to_regclass('public.governance_policy') IS NOT NULL")):
        raise AppError("请先应用0053治理策略增量迁移", code="GOVERNANCE_MIGRATION_REQUIRED", status_code=409)


@router.get("/policy")
async def read_policy(session: SessionDep, user: Manager):
    await _ready(session)
    oid = require_ontology_id(session)
    row = await session.scalar(select(GovernancePolicy).where(GovernancePolicy.ontology_id == oid))
    definition = await load_definition(session)
    graph = await policy_graph(session)
    states = effective_policies(definition, graph)
    roles = await role_catalog(session)
    return {
        "revision": row.revision if row else 0,
        "definition": definition,
        "resources": graph["nodes"],
        "effective": states,
        "roles": roles,
    }


@router.put("/policy")
async def save_policy(body: PolicySave, session: SessionDep, user: Manager, request: Request):
    await _ready(session)
    oid = require_ontology_id(session)
    if user.ontology_id != oid or user.ontology_role not in ("owner", "editor"):
        raise Forbidden("只有当前本体可编辑成员可管理治理策略")
    definition = body.definition.model_dump(mode="json")
    graph = await policy_graph(session)
    nodes = {n["id"] for n in graph["nodes"]}
    roles = {row["code"] for row in await role_catalog(session)}
    if any(g["role"] not in roles for g in definition["grants"]):
        raise AppError("策略角色不存在", code="GOVERNANCE_POLICY_INVALID", status_code=422)
    if any(
        r["resource"] not in nodes or not set(r["dependencies"]) <= nodes for r in definition["resources"]
    ):
        raise AppError(
            "策略资源或依赖不在当前已发布定义中", code="GOVERNANCE_POLICY_INVALID", status_code=422
        )
    effective_policies(definition, graph)
    row = await session.scalar(
        select(GovernancePolicy).where(GovernancePolicy.ontology_id == oid).with_for_update()
    )
    if body.revision != (row.revision if row else 0):
        raise VersionConflict("治理策略已变化，请重新读取后保存")
    if row is None:
        row = GovernancePolicy(ontology_id=oid, definition=definition, revision=1, created_by=user.id)
        session.add(row)
    else:
        row.definition = definition
        row.revision += 1
    await session.flush()
    session.info.pop("governance_definition", None)
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        ip=request.client.host if request.client else "",
        action="更新治理策略",
        target=f"本体{oid}治理策略 v{row.revision}",
        result="成功",
        created_by=user.id,
    )
    return {"revision": row.revision}


@router.get("/my-access", dependencies=[Depends(require_api_scope("meta:read"))])
async def my_access(session: SessionDep, user: CurrentUserDep):
    from app.modules.governance.policy import policy_roles

    definition = await load_definition(session)
    roles = await policy_roles(session, user)
    codes = {p for g in definition["grants"] if g["role"] in roles for p in g["purposes"]}
    return {
        "purposes": [p for p in definition["purposes"] if p["code"] in codes],
        "selected": session.info.get("data_purpose"),
        "configured": bool(definition["resources"]),
    }


@router.get("/policy/effective")
async def policy_effective(
    session: SessionDep, user: Manager, role: str, purpose: str, operation: str = "read"
):
    if role not in {row["code"] for row in await role_catalog(session)}:
        raise Forbidden("角色不属于当前本体")
    definition = await load_definition(session)
    states = effective_policies(definition, await policy_graph(session))
    return {
        resource: decide(definition, states, [role], resource, operation, purpose)
        for resource, state in states.items()
        if state["protected"]
    }


@router.get("/relation-paths")
async def relation_paths(session: SessionDep, user: Manager):
    from app.modules.governance.relation_scopes import options

    return {"items": await options(session)}


@router.get("/relation-values")
async def relation_values(
    session: SessionDep,
    user: Manager,
    path: str,
    object_type_code: Annotated[str, Query(alias="objectTypeCode", max_length=64)],
    kw: str = "",
):
    from app.modules.governance.relation_scopes import values

    rows = await values(session, user, object_type_code, path, kw=kw)
    return {"items": rows[:200], "hasMore": len(rows) > 200}


@router.get("/effective", dependencies=[Depends(require_api_scope("meta:read"))])
async def effective(session: SessionDep, user: CurrentUserDep, resource: str, operation: str = "read"):
    from app.modules.governance.policy import policy_roles

    definition = await load_definition(session)
    graph = await policy_graph(session)
    from app.modules.static.lineage import current_lineage

    visible = await current_lineage(session, user)
    if resource not in {n["id"] for n in visible["nodes"]}:
        raise Forbidden("资源不可见")
    states = effective_policies(definition, graph)
    decision = decide(
        definition,
        states,
        await policy_roles(session, user),
        resource,
        operation,
        session.info.get("data_purpose"),
    )
    return decision
