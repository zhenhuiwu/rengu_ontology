"""Source-bound effect inspection and explicit safe requeue; never resend unknown outcomes."""

from sqlalchemy import func, select

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.common.outbox import OutboxEvent
from app.common.pagination import PageParams
from app.core import clock
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.schema import _fmt_dt
from app.modules.dynamic.models import ActionRun
from app.modules.dynamic.row_scope import scoped_instances
from app.modules.dynamic.run_visibility import params_unavailable
from app.modules.integration.action_effects import CLAIMED, RETRY_SCHEDULED, TOPIC, recheck_effect


async def visible_run(session, user, code):
    check_func(user, "action-center", "view")
    statement = await scoped_instances(
        session, select(ActionRun).where(ActionRun.code == code), user, ActionRun.obj_kind, ActionRun.obj_id
    )
    run = await session.scalar(statement)
    if run is None:
        raise NotFound("执行记录不存在或不可见")
    if await params_unavailable(session, user, run):
        raise Forbidden("执行记录引用的对象不可核验，不能读取副作用")
    return run


def retry_reason(row):
    data = row.payload or {}
    state, history = data.get("state"), data.get("attempts") or []
    if row.dispatched_at is not None or state == "succeeded":
        return "副作用已成功，不能重复投递"
    if row.last_error == CLAIMED or state in ("unknown", "queued", "retrying"):
        return "结果未知或正在排队，不能盲目重复外部写入"
    if state == "rejected" and not history:
        return None  # No external send occurred; authority/configuration is rechecked before requeue.
    if state == "failed" and history:
        last = history[-1]
        status = last.get("httpStatus")
        if (
            last.get("retryable") is True
            and isinstance(status, int)
            and (status == 429 or 500 <= status <= 599)
        ):
            return None
    return "没有明确可重试的失败证据"


def effect_out(row, user):
    data = row.payload or {}
    reason = retry_reason(row)
    allowed = user.func.get("external-systems") == "manage" and user.func.get("action-center") == "manage"
    return {
        "id": str(row.id),
        "runId": data.get("runId"),
        "state": data.get("state", "unknown"),
        "attempts": row.attempts,
        "lastError": row.last_error,
        "createdAt": _fmt_dt(row.created_at),
        "availableAt": _fmt_dt(row.available_at),
        "dispatchedAt": _fmt_dt(row.dispatched_at) if row.dispatched_at else None,
        "canRetry": allowed and reason is None,
        "retryReason": reason or (None if allowed else "需要动作中心及外部系统管理权限"),
        "history": [
            {
                key: attempt.get(key)
                for key in ("attempt", "at", "httpStatus", "error", "retryable", "signed", "elapsedMs")
            }
            for attempt in data.get("attempts", [])
        ],
        "manualRetries": data.get("manualRetries", []),
    }


def effect_scope(session, run):
    return (
        OutboxEvent.topic == TOPIC,
        OutboxEvent.payload["ontologyId"].as_string() == str(require_ontology_id(session)),
        OutboxEvent.payload["runId"].as_string() == run.code,
    )


async def list_effects(session, user, code, page: PageParams):
    run = await visible_run(session, user, code)
    scope = effect_scope(session, run)
    total = int(await session.scalar(select(func.count()).select_from(OutboxEvent).where(*scope)) or 0)
    page = page.clamp(total)
    rows = await session.scalars(
        select(OutboxEvent).where(*scope).order_by(OutboxEvent.id.desc()).offset(page.offset).limit(page.size)
    )
    return {
        "items": [effect_out(row, user) for row in rows],
        "total": total,
        "page": page.page,
        "size": page.size,
    }


async def retry_effect(session, user, code, identity, attempts, state, *, ip=""):
    check_func(user, "action-center", "manage")
    check_func(user, "external-systems", "manage")
    run = await visible_run(session, user, code)
    row = await session.scalar(
        select(OutboxEvent).where(*effect_scope(session, run), OutboxEvent.id == identity).with_for_update()
    )
    if row is None:
        raise NotFound("副作用记录不存在")
    if row.attempts != attempts or row.payload.get("state") != state:
        raise Conflict("副作用状态已变化，请刷新后重试", code="VERSION_CONFLICT")
    reason = retry_reason(row)
    if reason:
        raise Conflict(reason, code="EFFECT_RETRY_UNSAFE")
    # Check both the operator and original execution principal using retained source/definition values.
    from app.modules.integration.action_edits import execution_definition, target_access

    await target_access(session, user, run, await execution_definition(session, run))
    await recheck_effect(session, row.payload)
    retry = {"at": clock.now().isoformat(), "actor": user.id, "fromState": state}
    row.payload = {
        **row.payload,
        "state": "retrying",
        "manualRetries": [*row.payload.get("manualRetries", []), retry],
    }
    row.available_at, row.last_error = clock.now(), RETRY_SCHEDULED
    from app.modules.admin.repo import insert_audit

    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="重试动作副作用",
        target=f"{run.code} · {row.event_id}",
        created_by=user.id,
        ip=ip,
    )
    await session.flush()
    return effect_out(row, user)
