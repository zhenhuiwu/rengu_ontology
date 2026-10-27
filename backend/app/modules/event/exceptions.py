"""Permission-scoped exception inbox and explicit, audited human decisions."""

import hashlib
import json
from typing import Literal

from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import and_, exists, func, or_, select

from app.common.auth import check_func, resolve_user, row_filter
from app.common.context import check_writable
from app.common.row_filter import is_deny_all, to_sql
from app.common.sql_guard import governed_tables
from app.core import clock
from app.core.errors import AppError, Conflict, Forbidden, NotFound, VersionConflict
from app.core.schema import ApiModel
from app.db.dialects import get_adapter
from app.modules.action.condition_expr import Unavailable
from app.modules.event.delivery import (
    aggregate,
    authorize_object,
    check_order,
    delivery_key,
    http_target,
    object_order_lock,
    route_fingerprint,
    route_for,
)
from app.modules.event.delivery_history import record, safe_result
from app.modules.event.models import Event, EventDelivery, EventDeliveryRecord, EventOutcome, EventType

EXCEPTION_STATES = ("unknown", "failed", "quarantined", "pending")
RETRY_ERRORS = frozenset(
    {
        "DELIVERY_ROUTE_NOT_CONFIGURED",
        "DELIVERY_ROUTE_CONFLICT",
        "DELIVERY_IDENTITY_NOT_CONFIGURED",
        "HTTP_DELIVERY_DISABLED",
        "HTTP_ROUTE_NOT_CONFIGURED",
        "HTTP_ROUTE_INVALID",
        "HTTP_IDEMPOTENCY_NOT_CONFIRMED",
        "HTTP_SUBSCRIBER_REJECTED",
        "TEST_CHANNEL_NOT_CONFIGURED",
        "TEST_CHANNEL_MUST_BE_SEPARATE",
        "ORDERING_CHANNEL_NOT_CONFIGURED",
        "EVENT_OBJECT_FORBIDDEN",
        "EVENT_OBJECT_AMBIGUOUS",
        "NO_PERMISSION",
        "UNAUTHORIZED",
    }
)


class DispositionIn(ApiModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    decision: Literal["received", "skip", "retry", "supplement"]
    reason: str = Field(min_length=5, max_length=1000)
    evidence: str = Field(min_length=5, max_length=2000)
    authorized: Literal[True]

    @field_validator("reason", "evidence")
    @classmethod
    def meaningful(cls, value):
        value = value.strip()
        if len(value) < 5:
            raise ValueError("请填写至少5个字的明确理由或核查证据")
        return value


async def visible_events(user):
    scopes = [and_(Event.is_test.is_(True), Event.created_by == user.id)]
    for mapping in governed_tables(user.ontology_id).values():
        if mapping.inherit or "code" not in mapping.table.c:
            continue
        expr = await row_filter(user, mapping.object_type)
        if is_deny_all(expr):
            continue
        query = select(mapping.table.c.code).where(mapping.table.c.code == Event.object_id)
        if expr is not None:
            query = query.where(to_sql(expr, mapping.columns, user.attrs, mapping.links))
        scopes.append(and_(Event.is_test.is_(False), Event.obj_kind == mapping.object_type, exists(query)))
    return or_(*scopes)


async def authorize(session, user, event, level="view"):
    check_func(user, "event-types", level)
    if event.is_test:
        if event.created_by != user.id:
            raise Forbidden("只能处理本人创建的测试投递记录")
    else:
        await authorize_object(session, user, event, level)


def output(row, event):
    return {
        "id": str(row.id),
        "eventId": event.code,
        "name": row.subscriber_name,
        "state": row.state,
        "attempts": row.attempts,
        "error": row.error,
        "result": safe_result(row.result),
        "version": row.lock_version,
        "startedAt": row.started_at,
        "finishedAt": row.finished_at,
        "isTest": event.is_test,
        "objectId": event.object_id,
        "idempotencyKey": delivery_key(event, row.subscriber_name),
    }


async def inbox(session, user, type_code, state, keyword, page, size):
    event_type = await session.scalar(select(EventType).where(EventType.code == type_code))
    if event_type is None:
        raise NotFound("事件类型不存在")
    query = (
        select(EventDelivery, Event)
        .join(Event, Event.id == EventDelivery.event_id)
        .where(
            Event.event_type_id == event_type.id,
            await visible_events(user),
            EventDelivery.state.in_((state,) if state else EXCEPTION_STATES),
        )
    )
    if keyword:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                Event.code.ilike(f"%{escaped}%", escape="\\"),
                EventDelivery.subscriber_name.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    page = min(page, max(1, (total + size - 1) // size))
    rows = await session.execute(
        query.order_by(Event.occurred_at.desc(), EventDelivery.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    return {"items": [output(row, event) for row, event in rows], "total": total, "page": page, "size": size}


async def detail(session, user, delivery_id, page, size):
    row = await session.get(EventDelivery, delivery_id)
    if row is None:
        raise NotFound("投递记录不存在")
    event = await session.get(Event, row.event_id)
    await authorize(session, user, event)
    query = select(EventDeliveryRecord).where(EventDeliveryRecord.delivery_id == row.id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    page = min(page, max(1, (total + size - 1) // size))
    history = await session.scalars(
        query.order_by(EventDeliveryRecord.id.desc()).offset((page - 1) * size).limit(size)
    )
    decisions = []
    for decision in ("received", "skip", "retry", "supplement"):
        try:
            validate_decision(row, decision)
            decisions.append(decision)
        except Conflict:
            pass
    return {
        **output(row, event),
        "canManage": user.func.get("event-types") == "manage",
        "decisions": decisions,
        "history": {
            "total": total,
            "page": page,
            "size": size,
            "items": [
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "attempt": item.attempt,
                    "state": item.state,
                    "error": item.error,
                    "result": safe_result(item.result),
                    "at": item.created_at,
                    "actor": item.created_by,
                    "detail": {
                        key: value for key, value in (item.detail or {}).items() if key != "routeFingerprint"
                    },
                }
                for item in history
            ],
        },
    }


def validate_decision(row, decision):
    if row.state not in EXCEPTION_STATES:
        raise Conflict("当前状态已变化，不能重复处置", code="DELIVERY_STATE_CHANGED")
    if row.state == "quarantined" and decision != "skip":
        raise Conflict("迟到事件只能记录人工跳过；补投会破坏已经发生的顺序", code="LATE_DELIVERY_BLOCKED")
    if decision == "received" and (not row.attempts or row.state not in ("unknown", "failed")):
        raise Conflict("仅已尝试且结果未知或失败的投递可记录已接收结论")
    if decision == "retry" and not (
        row.state == "unknown" or (row.state == "failed" and row.error in RETRY_ERRORS)
    ):
        raise Conflict("当前失败原因不允许重试，请先核查原定义及业务结果", code="DELIVERY_RETRY_BLOCKED")
    if decision == "supplement" and not (
        row.state == "pending" and not row.attempts and row.error in RETRY_ERRORS
    ):
        raise Conflict("仅尚未尝试且通道条件可修复的投递可申请补投", code="DELIVERY_RETRY_BLOCKED")


async def validate_requeue(session, user, event, row):
    from app.modules.event.ingress import validate_fields
    from app.modules.event.schemas import FieldOut
    from app.modules.event.simulation import filter_matches
    from app.modules.versioning.repo import ReleaseRepo

    definition = event.definition_snapshot
    if (
        event.is_seed
        or event.outcome == EventOutcome.deduped
        or not isinstance(definition, dict)
        or not isinstance(event.payload, dict)
        or not definition.get("id")
        or not definition.get("version")
        or not isinstance(definition.get("payload"), list)
        or not isinstance(definition.get("subscribers"), list)
        or row.subscription not in definition["subscribers"]
        or row.subscription.get("status") != "启用"
        or (not event.is_test and (not event.ingested_at or not event.envelope_hash))
    ):
        raise Conflict("原始事件或订阅快照不完整，不能补投或重试", code="DELIVERY_SNAPSHOT_INCOMPLETE")
    fields = [FieldOut.model_validate(field) for field in definition["payload"]]
    validate_fields(fields, event.payload)
    if not filter_matches(row.subscription.get("filter", ""), fields, event.payload):
        raise Conflict("原订阅过滤条件不匹配，不能补投")
    route = route_for(event, row)
    if not route or (route.get("action_type") and route.get("rule_code")):
        raise Conflict("请先修复明确的订阅通道配置", code="DELIVERY_ROUTE_NOT_CONFIGURED")
    if event.is_test and (route.get("rule_code") or route.get("action_type")):
        raise Conflict("测试事件不能重试真实规则或动作")
    if not event.is_test:
        if not route.get("user"):
            raise Conflict("投递身份尚未配置")
        await authorize_object(session, await resolve_user(session, route["user"]), event)
        ordered = route.get("ordering") == "object" or definition.get("ordering") not in (None, "", "不保证")
        if ordered and not await check_order(session, event, row, route):
            raise Conflict("迟到事件不能补投", code="LATE_DELIVERY_BLOCKED")
    previous = list(
        await session.scalars(select(EventDeliveryRecord).where(EventDeliveryRecord.delivery_id == row.id))
    )
    if row.attempts:
        starts = [item for item in previous if item.kind == "attempt_started"]
        if not starts or any(
            (item.detail or {}).get("routeFingerprint") != route_fingerprint(route) for item in starts
        ):
            raise Conflict(
                "历史通道绑定缺失或接收方已改变，不能安全重试；请核查并记录人工结论",
                code="DELIVERY_ROUTE_BINDING_CHANGED",
            )
    if any(
        (receipt or {}).get(key)
        for receipt in [row.result, *(item.result for item in previous)]
        for key in ("actionId", "hitId", "alertId")
    ):
        raise Conflict("已有持久业务结果，请核对并记录人工结论，不能重复生成", code="DELIVERY_RESULT_EXISTS")
    if route.get("rule_code") or route.get("action_type"):
        release = await ReleaseRepo(session).latest()
        if release is None or release.id != event.release_id:
            raise Conflict("入站绑定版本已变化，不能用当前业务定义重试")
        if route.get("rule_code"):
            from app.modules.dynamic.rules_models import RuleHit

            source = f"{event.code}\0{route['rule_code']}"
            if event.ontology_id != 1:
                source = f"{event.ontology_id}\0{source}"
            hit_code = "RH-" + hashlib.sha256(source.encode()).hexdigest()[:60]
            existing = await session.scalar(select(RuleHit.id).where(RuleHit.code == hit_code))
        else:
            from app.modules.dynamic.models import ActionRun

            identity = [event.code, route["action_type"]]
            if event.ontology_id != 1:
                identity.insert(0, event.ontology_id)
            key = "evt-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            existing = await session.scalar(select(ActionRun.id).where(ActionRun.idem_key == key))
        if existing is not None:
            raise Conflict("已存在原事件的业务结果，请核对后记录人工结论", code="DELIVERY_RESULT_EXISTS")
    else:
        http_target(route, event.is_test)


async def dispose(session, user, ctx, delivery_id, body):
    check_writable(ctx.ver, ctx.preview_as)
    check_func(user, "event-types", "manage")
    candidate = await session.get(EventDelivery, delivery_id)
    if candidate is None:
        raise NotFound("投递记录不存在")
    event = await session.get(Event, candidate.event_id)
    await authorize(session, user, event, "manage")
    if route_for(event, candidate).get("rule_code"):
        await get_adapter(session).xact_lock(session, "ontology.publish")
    if not event.is_test:
        await get_adapter(session).xact_lock(
            session, object_order_lock(event.event_type_id, event.obj_kind, event.object_id)
        )
    row = await session.scalar(
        select(EventDelivery)
        .where(EventDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    event = await session.scalar(select(Event).where(Event.id == row.event_id).with_for_update(of=Event))
    if row.lock_version != body.version:
        raise VersionConflict()
    validate_decision(row, body.decision)
    if body.decision in ("retry", "supplement"):
        try:
            await validate_requeue(session, user, event, row)
        except Unavailable as exc:
            raise AppError("补投条件仍未满足，请先修复通道并核查", code=str(exc), status_code=409) from None
    before = {
        "state": row.state,
        "error": row.error,
        "result": safe_result(row.result),
        "version": row.lock_version,
    }
    record(session, row, "before_disposition", actor=user.id)
    if body.decision in ("retry", "supplement"):
        row.state, row.next_at = "pending", clock.now()
        row.started_at, row.finished_at = None, None
        row.error, row.result = None, None
    else:
        row.state = "confirmed" if body.decision == "received" else "skipped"
        row.finished_at, row.next_at = clock.now(), None
        # Original transport receipt/error remain intact; human evidence is stored separately.
    record(
        session,
        row,
        "disposition",
        actor=user.id,
        detail={
            "decision": body.decision,
            "reason": body.reason,
            "evidence": body.evidence,
            "authorized": True,
            "before": before,
            "idempotencyKey": delivery_key(event, row.subscriber_name),
        },
    )
    await aggregate(session, event)
    await session.flush()
    return output(row, event)
