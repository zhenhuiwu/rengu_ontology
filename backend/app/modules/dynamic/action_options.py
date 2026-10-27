"""Read-only action launch metadata; reuse execution checks without dispatching."""

from types import SimpleNamespace

from sqlalchemy import func, select

from app.common.auth import check_func, resolve_user
from app.common.pagination import Page
from app.common.row_filter import apply_row_filter
from app.core.errors import AppError, NotFound
from app.modules.action.models import ActionType, TypeStatus
from app.modules.integration.action_edits import (
    execution_definition,
    existing_row,
    mapping_json,
    object_binding,
    object_operation,
    relation_binding,
)
from app.modules.integration.execution import submit_adapter
from app.modules.integration.models import ExtSystem


async def launch_definition(session, user, catalog, code, *, identity=None):
    check_func(user, "action-center", "manage")
    action = await session.scalar(select(ActionType).where(ActionType.code == code))
    if action is None:
        raise NotFound("动作类型不存在")
    run = SimpleNamespace(
        action_type_code=code,
        type_version=action.version,
        sys_code=action.system.code if action.system else None,
    )
    definition = await execution_definition(session, run)
    meta = catalog.action_meta(code) or {}
    if not meta.get("approvalConfigured"):
        raise AppError("动作未配置可用审批人", code="ACTION_APPROVAL_NOT_CONFIGURED", status_code=503)
    if not definition.get("targetKind"):
        raise AppError("动作没有明确的目标对象类型", code="ACTION_TARGET_UNAVAILABLE", status_code=503)
    obj, governed = await object_binding(session, definition["targetKind"])
    # Resolve every explicit operation binding, including both endpoints, before advertising availability.
    for edit in definition["writeback"]["edits"]:
        mapping_json(edit.get("field"))
        if edit.get("op") == "关联":
            await relation_binding(session, edit.get("obj"))
        else:
            await object_binding(session, edit.get("obj"))
    from app.modules.action.execution_options import action_operation_objects

    affected_objects = await action_operation_objects(session, definition)
    for affected in affected_objects:
        await object_operation(session, user, affected, definition)
    if meta.get("approvalRequired"):
        try:
            approver = await resolve_user(session, meta["approverId"])
            check_func(approver, "action-center", "manage")
            if approver.ontology_role not in ("owner", "editor"):
                raise AppError("审批人仅有只读权限")
            for affected in affected_objects:
                await object_operation(session, approver, affected, definition)
            if identity is not None:
                await existing_row(session, approver, obj, governed, identity, lock=False)
        except AppError:
            raise AppError(
                "审批人没有此本体目标动作的处理权限", code="ACTION_APPROVAL_NOT_CONFIGURED", status_code=503
            ) from None
    if run.sys_code:
        system = await session.scalar(select(ExtSystem).where(ExtSystem.code == run.sys_code))
        if system is None:
            raise AppError("外部系统不存在", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)
        try:
            submit_adapter(system, definition)  # validates local configuration only
        except AppError:
            raise AppError(
                "外部动作执行通道未配置或不可用", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503
            ) from None
    return definition, obj, governed


async def options(session, user, catalog):
    actions = list(
        await session.scalars(
            select(ActionType)
            .where(
                ActionType.retired_release_id.is_(None),
                ActionType.status.in_((TypeStatus.published, TypeStatus.changing)),
            )
            .order_by(ActionType.code)
        )
    )
    result = []
    for action in actions:
        reason, definition = None, None
        try:
            definition, _, _ = await launch_definition(session, user, catalog, action.code)
        except AppError as exc:
            reason = exc.message
        result.append(
            {
                "id": action.code,
                "name": action.name,
                "version": action.version,
                "kind": action.target_kind,
                "available": reason is None,
                "reason": reason,
                "params": [
                    {k: v for k, v in p.items() if not k.startswith("_")} for p in definition["params"]
                ]
                if definition
                else [],
                "approval": action.approval,
            }
        )
    return result


async def targets(session, user, catalog, code, kw, page):
    _, _, governed = await launch_definition(session, user, catalog, code)
    table = governed.table
    statement = select(table.c.code).where(
        table.c.code.in_(select(table.c.code).group_by(table.c.code).having(func.count() == 1))
    )
    statement = await apply_row_filter(
        statement, user, governed.object_type, governed.columns, governed.links
    )
    meta = catalog.action_meta(code) or {}
    if meta.get("approvalRequired"):
        approver = await resolve_user(session, meta["approverId"])
        statement = await apply_row_filter(
            statement, approver, governed.object_type, governed.columns, governed.links
        )
    if kw and kw.strip():
        statement = statement.where(table.c.code.contains(kw.strip(), autoescape=True))
    total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    p = page.clamp(total)
    codes = list(await session.scalars(statement.order_by(table.c.code).offset(p.offset).limit(p.size)))
    return Page[dict].of([{"id": value, "label": value} for value in codes], total, page)
