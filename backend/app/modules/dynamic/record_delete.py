"""Persistent user deletion without removing audit rows or idempotency tombstones."""

from sqlalchemy import or_, select

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.common.outbox import OutboxEvent
from app.core import clock
from app.core.errors import AppError, NotFound, VersionConflict
from app.db.dialects import get_adapter
from app.modules.admin.repo import insert_audit
from app.modules.dynamic.models import IN_FLIGHT, ActionRun, ActionStatus, AlertOp, AlertStatus
from app.modules.integration.execution import CLAIMED, QUEUED
from app.modules.integration.models import ReconcileState, ReconcileTask, WritebackCall

TERMINAL = {ActionStatus.succeeded, ActionStatus.failed, ActionStatus.withdrawn, ActionStatus.reconciled}


def permission(user, page):
    return (
        user.func.get(page) == "manage"
        and user.ontology_role in ("owner", "editor")
        and not user.is_service_account
        and user.ontology_ids is None
    )


def action_state_reason(run):
    if run.deleted_at is not None:
        return "记录已删除"
    if run.status not in TERMINAL:
        return "动作尚未终结，待确认、审批中、执行中或结果未知的记录不能删除"
    return None


async def action_reason(session, run):
    if reason := action_state_reason(run):
        return reason
    if await session.scalar(
        select(WritebackCall.id)
        .where(WritebackCall.action_run_id == run.id, WritebackCall.msg.in_((QUEUED, CLAIMED)))
        .limit(1)
    ):
        return "仍有排队或已领取的外部调用，不能删除"
    if await session.scalar(
        select(ReconcileTask.id)
        .where(ReconcileTask.action_run_id == run.id, ReconcileTask.state != ReconcileState.resolved)
        .limit(1)
    ):
        return "仍有待查询或待人工确认的对账任务，不能删除"
    from app.modules.integration.action_effects import TOPIC

    if await session.scalar(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.topic == TOPIC,
            OutboxEvent.payload["runId"].as_string() == run.code,
            OutboxEvent.dispatched_at.is_(None),
        )
        .limit(1)
    ):
        return "仍有未完成的动作副作用投递，不能删除"
    return None


async def alert_reason(session, alert):
    if alert.deleted_at is not None:
        return "告警已删除"
    if alert.status not in (AlertStatus.resolved, AlertStatus.ignored):
        return "请先按现有处理流程解决或忽略告警，再删除终态记录"
    condition = ActionRun.alert_id == alert.id
    if alert.action_run_id:
        condition = or_(condition, ActionRun.id == alert.action_run_id)
    runs = list(await session.scalars(select(ActionRun).where(condition)))
    for run in runs:
        if run.status in IN_FLIGHT:
            return "仍有关联的待确认、审批中、执行中或结果未知动作，不能删除告警"
        if (reason := await action_reason(session, run)) and (
            run.deleted_at is None or reason != "记录已删除"
        ):
            return reason
    return None


async def can_delete(session, user, row, *, alert=False):
    if not permission(user, "alerts" if alert else "action-center"):
        return "需要本体工作空间的人工管理权限"
    return await (alert_reason(session, row) if alert else action_reason(session, row))


async def remove_action(svc, code, version):
    check_func(svc.user, "action-center", "manage")
    visible = await svc.get(code)
    row = await svc.session.scalar(
        select(ActionRun)
        .where(ActionRun.id == visible.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.deleted_at is not None:
        raise NotFound("动作记录不存在")
    if row.lock_version != version:
        raise VersionConflict("记录状态已变化，请刷新后重试")
    if reason := await can_delete(svc.session, svc.user, row):
        raise AppError(reason, code="RECORD_DELETE_BLOCKED", status_code=409)
    row.deleted_at, row.deleted_by = clock.now(), svc.user.id
    await svc._log(row, "执行记录已删除；保留执行事实、幂等与审计，不重新执行", svc.user.id)
    await insert_audit(
        svc.session,
        actor=f"{svc.user.name}（{svc.user.id}）",
        action="删除动作记录",
        target=row.code,
        ip="",
        created_by=svc.user.id,
    )
    await svc.session.flush()


async def remove_alert(svc, code, version):
    check_func(svc.user, "alerts", "manage")
    visible = await svc.get(code)
    # Same stage fence, then run -> alert lock order. Never reverse the confirm path.
    await get_adapter(svc.session).xact_lock(
        svc.session, f"dynamic.alert.stage.{require_ontology_id(svc.session)}.{visible.code}"
    )
    condition = ActionRun.alert_id == visible.id
    if visible.action_run_id:
        condition = or_(condition, ActionRun.id == visible.action_run_id)
    await svc.session.scalars(
        select(ActionRun)
        .where(condition)
        .order_by(ActionRun.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = await svc.get(code, lock=True)
    if row.lock_version != version:
        raise VersionConflict("告警状态已变化，请刷新后重试")
    if reason := await can_delete(svc.session, svc.user, row, alert=True):
        raise AppError(reason, code="RECORD_DELETE_BLOCKED", status_code=409)
    row.deleted_at, row.deleted_by = clock.now(), svc.user.id
    await svc.repo.add_log(
        row,
        at=clock.now(),
        actor=svc.user.id,
        op=AlertOp.note,
        text="告警记录已删除；保留原处理状态、关联与审计",
    )
    await insert_audit(
        svc.session,
        actor=f"{svc.user.name}（{svc.user.id}）",
        action="删除告警记录",
        target=row.code,
        ip="",
        created_by=svc.user.id,
    )
    await svc.session.flush()
