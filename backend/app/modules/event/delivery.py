"""Durable subscriber execution. An HTTP acknowledgement or staged action is required for success."""

import hashlib
import json
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import exists, or_, select, tuple_

from app.common.auth import check_func, resolve_user, row_filter
from app.common.row_filter import is_deny_all, to_sql
from app.common.sql_guard import governed_tables
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.dialects import get_adapter
from app.modules.action.condition_expr import Unavailable
from app.modules.event.delivery_history import record, safe_result
from app.modules.event.models import Event, EventDelivery, EventDeliveryRecord, EventOutcome
from app.modules.event.schemas import FieldOut

INTERVAL = timedelta(minutes=1)


def routes() -> dict:
    value = get_settings().event_delivery_routes
    if value is None:
        return {}
    try:
        data = json.loads(value.get_secret_value())
        if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
            raise ValueError
        return data
    except (ValueError, TypeError):
        raise AppError("事件投递路由配置无效", code="EVENT_ROUTES_INVALID", status_code=503) from None


def delivery_key(event: Event, name: str) -> str:
    digest = hashlib.sha256(f"{event.code}\0{name}".encode()).hexdigest()
    return f"event-{digest}"


def http_target(route: dict, is_test: bool) -> str:
    settings = get_settings()
    if not settings.event_delivery_http_enabled:
        raise Unavailable("HTTP_DELIVERY_DISABLED")
    endpoint = route.get("test_url" if is_test else "url")
    if not isinstance(endpoint, str) or not endpoint:
        raise Unavailable("TEST_CHANNEL_NOT_CONFIGURED" if is_test else "HTTP_ROUTE_NOT_CONFIGURED")
    if is_test and endpoint == route.get("url"):
        raise Unavailable("TEST_CHANNEL_MUST_BE_SEPARATE")
    parts = urlsplit(endpoint)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if (
        parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or not parts.hostname
        or (parts.scheme != "https" and not (settings.env != "prod" and local and parts.scheme == "http"))
        or any(ord(char) < 32 or ord(char) == 127 for char in endpoint)
    ):
        raise Unavailable("HTTP_ROUTE_INVALID")
    if route.get("idempotent") is not True:
        raise Unavailable("HTTP_IDEMPOTENCY_NOT_CONFIRMED")
    return endpoint


async def authorize_object(session, user, event: Event, level: str = "manage") -> None:
    """A production payload leaves the process only for an explicitly granted real object row."""
    check_func(user, "event-types", level)
    mapping = next(
        (g for g in governed_tables().values() if g.object_type == event.obj_kind and not g.inherit), None
    )
    expr = await row_filter(user, event.obj_kind or "")
    if mapping is None or "code" not in mapping.table.c or "id" not in mapping.table.c or is_deny_all(expr):
        raise AppError("订阅身份无对象行权限", code="EVENT_OBJECT_FORBIDDEN", status_code=403)
    identities = list(
        await session.scalars(
            select(mapping.table.c.id).where(mapping.table.c.code == event.object_id).limit(2)
        )
    )
    if len(identities) != 1:
        raise AppError("事件对象身份缺失或不唯一", code="EVENT_OBJECT_AMBIGUOUS", status_code=403)
    query = select(mapping.table.c.id).where(mapping.table.c.id == identities[0])
    if expr is not None:
        query = query.where(to_sql(expr, mapping.columns, user.attrs, mapping.links))
    if await session.scalar(query.limit(1)) is None:
        raise AppError("对象不存在或不在订阅身份授权范围", code="EVENT_OBJECT_FORBIDDEN", status_code=403)


def route_for(event: Event, delivery: EventDelivery) -> dict:
    definition = event.definition_snapshot or {}
    return routes().get(f"{definition.get('id', '')}/{delivery.subscriber_name}", {})


def route_fingerprint(route: dict) -> str:
    # Credentials may rotate; recipient, execution identity and mapping must stay bound to the attempt.
    binding = {key: value for key, value in route.items() if key not in ("token", "test_token")}
    return hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def object_order_lock(event_type_id: int, obj_kind: str, object_id: str) -> str:
    # Shared with ingress: the claim sees every accepted predecessor before advancing the stream.
    return "event.order:" + json.dumps([event_type_id, obj_kind, object_id], ensure_ascii=False)


async def check_order(session, event: Event, delivery: EventDelivery, route: dict) -> bool:
    """Called under the object transaction lock; committed attempts are the durable watermark."""
    if route.get("ordering") != "object":
        raise Unavailable("ORDERING_CHANNEL_NOT_CONFIGURED")
    if not event.obj_kind or not event.object_id or not event.occurred_at or not event.code:
        raise Unavailable("ORDERING_OBJECT_IDENTITY_REQUIRED")
    stream = (
        Event.event_type_id == event.event_type_id,
        Event.obj_kind == event.obj_kind,
        Event.object_id == event.object_id,
        Event.is_seed.is_(False),
        Event.is_test.is_(False),
        Event.definition_snapshot.is_not(None),
        Event.outcome != EventOutcome.deduped,
    )
    position = tuple_(Event.occurred_at, Event.code.collate("C"))
    current = tuple_(event.occurred_at, event.code)
    later = await session.scalar(
        select(Event.code)
        .join(EventDelivery, EventDelivery.event_id == Event.id)
        .where(
            *stream,
            EventDelivery.attempts > 0,
            position > current,
        )
        .limit(1)
    )
    if later is not None:
        # A send cannot be undone; late arrivals are retained but never inserted before a claimed send.
        delivery.state, delivery.error = "quarantined", "ORDERING_LATE_EVENT_QUARANTINED"
        delivery.finished_at, delivery.next_at = clock.now(), None
        delivery.result = None
        return False
    # shortcut: reuse the type/time index; add an object-domain index if retained streams outgrow it.
    predecessors = await session.execute(
        select(Event.code, Event.definition_snapshot, EventDelivery.id)
        .outerjoin(EventDelivery, EventDelivery.event_id == Event.id)
        .where(
            *stream,
            position < current,
            or_(
                EventDelivery.id.is_(None),
                EventDelivery.state.not_in(("delivered", "confirmed", "skipped", "quarantined")),
            ),
        )
        .order_by(Event.occurred_at, Event.code.collate("C"))
    )
    for code, snapshot, predecessor_id in predecessors:
        if predecessor_id is None and isinstance(snapshot, dict):
            subscribers = snapshot.get("subscribers")
            if isinstance(subscribers, list) and not any(
                isinstance(s, dict) and s.get("status") == "启用" for s in subscribers
            ):
                continue
        delivery.result = {"blockedByEventId": code}
        raise Unavailable("ORDERING_PREDECESSOR_UNRESOLVED")
    return True


async def discover(session) -> None:
    """Only accepted new events have trustworthy definition snapshots; legacy rows remain explicit pending."""
    events = await session.scalars(
        select(Event)
        .where(
            Event.outcome == EventOutcome.pending,
            Event.is_seed.is_(False),
            Event.definition_snapshot.is_not(None),
            ~exists(select(EventDelivery.id).where(EventDelivery.event_id == Event.id)),
        )
        .order_by(Event.id)
        .with_for_update(of=Event, skip_locked=True)
        .limit(200)
    )
    for event in events:
        definition = event.definition_snapshot
        if not isinstance(definition, dict) or not isinstance(definition.get("subscribers"), list):
            event.outcome, event.summary = EventOutcome.pending, "待投递：入站定义快照不完整"
            continue
        subscribers = definition["subscribers"]
        names = [s.get("name") for s in subscribers if isinstance(s, dict)]
        if (
            len(names) != len(subscribers)
            or any(not isinstance(n, str) or not n.strip() for n in names)
            or len(set(names)) != len(names)
        ):
            event.outcome, event.summary = EventOutcome.pending, "待投递：订阅定义快照无效"
            continue
        for subscriber in subscribers:
            session.add(
                EventDelivery(
                    event_id=event.id,
                    subscriber_name=subscriber["name"],
                    subscription=subscriber,
                    state="skipped" if subscriber.get("status") != "启用" else "pending",
                    next_at=clock.now(),
                    finished_at=clock.now() if subscriber.get("status") != "启用" else None,
                    created_by="internal:event",
                    error="SUBSCRIPTION_DISABLED" if subscriber.get("status") != "启用" else None,
                )
            )
        if not subscribers:
            event.outcome, event.summary = EventOutcome.failed, "未投递：入站定义没有订阅方"
        else:
            await session.flush()
            await aggregate(session, event)
    await session.flush()


async def aggregate(session, event: Event) -> None:
    deliveries = list(await session.scalars(select(EventDelivery).where(EventDelivery.event_id == event.id)))
    event.subscriber_count = sum(d.state == "delivered" for d in deliveries)
    states = {d.state for d in deliveries}
    if states & {"pending", "processing"}:
        event.outcome = EventOutcome.partial if event.subscriber_count else EventOutcome.pending
    elif states & {"failed", "unknown", "quarantined"}:
        event.outcome = EventOutcome.partial if event.subscriber_count else EventOutcome.failed
    elif event.subscriber_count:
        event.outcome = (
            EventOutcome.alert_created
            if any(
                d.state == "delivered"
                and (d.result or {}).get("status") == "alert_created"
                and (d.result or {}).get("alertId")
                for d in deliveries
            )
            else EventOutcome.delivered
        )
    elif states & {"confirmed", "skipped"}:
        event.outcome = EventOutcome.partial
    else:
        event.outcome = EventOutcome.pending
    pending = sum(d.state in ("pending", "processing") for d in deliveries)
    failed = sum(d.state in ("failed", "unknown", "quarantined") for d in deliveries)
    skipped = sum(d.state == "skipped" for d in deliveries)
    confirmed = sum(d.state == "confirmed" for d in deliveries)
    event.summary = (
        f"订阅投递：成功 {event.subscriber_count}，人工确认 {confirmed}，待处理 {pending}，"
        f"失败/未知 {failed}，跳过 {skipped}"
    )
    if deliveries and not states & {"pending", "processing"} and event.ingested_at:
        event.latency_ms = max(0, round((clock.now() - event.ingested_at).total_seconds() * 1000))


async def prepare(session, delivery_id: int) -> bool:
    """Commit processing before any side effect; a crashed worker never blindly repeats a send."""
    from app.modules.event.simulation import filter_matches

    candidate = await session.get(EventDelivery, delivery_id)
    if candidate is None:
        return False
    event = await session.get(Event, candidate.event_id)
    if not event.is_test:
        await get_adapter(session).xact_lock(
            session, object_order_lock(event.event_type_id, event.obj_kind, event.object_id)
        )
    delivery = await session.scalar(
        select(EventDelivery)
        .where(EventDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None or delivery.state != "pending":
        return False
    before = (delivery.state, delivery.error, delivery.result)
    previous = {"previousError": before[1], "previousResult": safe_result(before[2])}
    delivery.result = None
    try:
        definition = event.definition_snapshot
        if (
            not isinstance(definition, dict)
            or any(key not in definition for key in ("id", "version", "objKind", "payload", "subscribers"))
            or not isinstance(definition.get("payload"), list)
            or not isinstance(definition.get("subscribers"), list)
            or not definition.get("id")
            or not definition.get("version")
            or (not event.is_test and (not event.ingested_at or not event.envelope_hash))
        ):
            raise Unavailable("DELIVERY_SNAPSHOT_INCOMPLETE")
        fields = [FieldOut.model_validate(f) for f in event.definition_snapshot["payload"]]
        try:
            matched = filter_matches(delivery.subscription.get("filter", ""), fields, event.payload or {})
        except Unavailable:
            delivery.state, delivery.error = "failed", "FILTER_NOT_EXECUTABLE"
            delivery.finished_at = clock.now()
            record(session, delivery, "preflight", detail=previous)
            await aggregate(session, event)
            return False
        if not matched:
            delivery.state, delivery.error = "skipped", "FILTER_NOT_MATCHED"
            delivery.finished_at = clock.now()
            record(session, delivery, "preflight", detail=previous)
            await aggregate(session, event)
            return False
        route = route_for(event, delivery)
        if not route:
            raise Unavailable("DELIVERY_ROUTE_NOT_CONFIGURED")
        if route.get("action_type") and route.get("rule_code"):
            raise Unavailable("DELIVERY_ROUTE_CONFLICT")
        if event.is_test and (route.get("action_type") or route.get("rule_code")):
            raise Unavailable("BUSINESS_TEST_CHANNEL_NOT_CONFIGURED")
        if route.get("rule_code") and (
            delivery.subscription.get("kind") != "规则"
            or delivery.subscription.get("ruleId") not in (None, "", route["rule_code"])
        ):
            raise Unavailable("RULE_SUBSCRIPTION_MISMATCH")
        if not event.is_test:
            if not route.get("user"):
                raise Unavailable("DELIVERY_IDENTITY_NOT_CONFIGURED")
            user = await resolve_user(session, route["user"])
            await authorize_object(session, user, event)
            ordered = route.get("ordering") == "object" or event.definition_snapshot.get("ordering") not in (
                None,
                "",
                "不保证",
            )
            if ordered and not await check_order(session, event, delivery, route):
                record(session, delivery, "preflight", detail=previous)
                await aggregate(session, event)
                return False
        if not (route.get("action_type") or route.get("rule_code")):
            http_target(route, event.is_test)
        delivery.state, delivery.started_at = "processing", clock.now()
        delivery.attempts += 1
        delivery.error, delivery.result = None, None
        record(
            session,
            delivery,
            "attempt_started",
            detail={
                "routeFingerprint": route_fingerprint(route),
                **previous,
            },
        )
        return True
    except (Unavailable, AppError) as exc:
        delivery.error = exc.code if isinstance(exc, AppError) else str(exc)
        delivery.next_at = clock.now() + INTERVAL
        if before != (delivery.state, delivery.error, delivery.result):
            record(session, delivery, "preflight", detail=previous)
        await aggregate(session, event)
        return False
    except (ValueError, TypeError, KeyError):
        delivery.state, delivery.error = "failed", "DELIVERY_DEFINITION_INVALID"
        delivery.finished_at = clock.now()
        record(session, delivery, "preflight", detail=previous)
        await aggregate(session, event)
        return False


async def execute(session, delivery_id: int) -> None:
    candidate = await session.get(EventDelivery, delivery_id)
    if candidate is None or candidate.state != "processing":
        return
    event = await session.get(Event, candidate.event_id)
    if route_for(event, candidate).get("rule_code"):
        # Publication precedes object/event locks, matching the public rule entry point.
        await get_adapter(session).xact_lock(session, "ontology.publish")
    if not event.is_test:
        await get_adapter(session).xact_lock(
            session, object_order_lock(event.event_type_id, event.obj_kind, event.object_id)
        )
    delivery = await session.scalar(
        select(EventDelivery)
        .where(EventDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if delivery is None or delivery.state != "processing":
        return
    event = await session.scalar(select(Event).where(Event.id == delivery.event_id).with_for_update(of=Event))
    try:
        route = route_for(event, delivery)
        started = await session.scalar(
            select(EventDeliveryRecord)
            .where(
                EventDeliveryRecord.delivery_id == delivery.id,
                EventDeliveryRecord.kind == "attempt_started",
                EventDeliveryRecord.attempt == delivery.attempts,
            )
            .order_by(EventDeliveryRecord.id.desc())
            .limit(1)
        )
        if started is None or (started.detail or {}).get("routeFingerprint") != route_fingerprint(route):
            raise Unavailable("DELIVERY_ROUTE_BINDING_CHANGED")
        if route.get("action_type") and route.get("rule_code"):
            raise Unavailable("DELIVERY_ROUTE_CONFLICT")
        if route.get("rule_code"):
            if event.is_test:
                raise Unavailable("BUSINESS_TEST_CHANNEL_NOT_CONFIGURED")
            if delivery.subscription.get("kind") != "规则" or delivery.subscription.get("ruleId") not in (
                None,
                "",
                route["rule_code"],
            ):
                raise Unavailable("RULE_SUBSCRIPTION_MISMATCH")
            from app.contracts.rules import dispatch_event_rule

            user = await resolve_user(session, route["user"])
            await authorize_object(session, user, event)
            async with session.begin_nested():
                result = await dispatch_event_rule(
                    session, user, rule_code=route["rule_code"], event_id=event.code
                )
            delivery.result = {
                key: result[key]
                for key in (
                    "ruleId",
                    "ruleVersion",
                    "hitId",
                    "alertId",
                    "status",
                    "actionStatus",
                    "actionReason",
                )
                if key in result
            }
        elif route.get("action_type"):
            if event.is_test:
                raise Unavailable("ACTION_TEST_CHANNEL_NOT_CONFIGURED")
            from app.contracts.actions import stage_event_action
            from app.modules.versioning.repo import ReleaseRepo

            user = await resolve_user(session, route["user"])
            await authorize_object(session, user, event)
            release = await ReleaseRepo(session).latest()
            if release is None or release.id != event.release_id:
                raise Unavailable("ACTION_RELEASE_CHANGED_REQUIRES_REVIEW")
            mapping = route.get("params")
            if not isinstance(mapping, dict) or any(
                not isinstance(v, str) or v not in (event.payload or {}) for v in mapping.values()
            ):
                raise Unavailable("ACTION_PARAMETER_MAPPING_INVALID")
            async with session.begin_nested():
                result = await stage_event_action(
                    session,
                    user,
                    action_type_code=route["action_type"],
                    event_id=event.code,
                    params={k: event.payload[v] for k, v in mapping.items()},
                    obj_kind=event.obj_kind,
                    obj_id=event.object_id,
                )
            delivery.result = {"actionId": result["id"], "status": result["status"]}
        else:
            # Configuration is rechecked after the claim transaction; it may have changed.
            endpoint = http_target(route, event.is_test)
            if not event.is_test:
                await authorize_object(session, await resolve_user(session, route["user"]), event)
            headers = {"Idempotency-Key": delivery_key(event, delivery.subscriber_name)}
            token = route.get("test_token" if event.is_test else "token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
            envelope = {
                "eventId": event.code,
                "eventType": event.definition_snapshot["id"],
                "occurredAt": event.occurred_at.isoformat(),
                "objectType": event.producer_object_type,
                "objectId": event.object_id,
                "payload": event.payload,
                "isTest": event.is_test,
                "ontologyVersion": event.release.ver if event.release else None,
                "typeVersion": event.definition_snapshot.get("version"),
                "traceId": event.trace_id,
            }
            async with (
                httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client,
                client.stream("POST", endpoint, json=envelope, headers=headers) as response,
            ):
                delivery.result = {"httpStatus": response.status_code}
                if not 200 <= response.status_code < 300:
                    raise Unavailable("HTTP_SUBSCRIBER_REJECTED")
        delivery.state, delivery.error = "delivered", None
    except httpx.RequestError:
        delivery.state, delivery.error = "unknown", "HTTP_RESULT_UNKNOWN_REQUIRES_REVIEW"
    except (AppError, Unavailable) as exc:
        delivery.state = "failed"
        delivery.error = exc.code if isinstance(exc, AppError) else str(exc)
    except Exception:
        # Do not log transport/configuration exceptions: their text can contain credentials.
        delivery.state, delivery.error = "unknown", "EXECUTION_INTERRUPTED_REQUIRES_REVIEW"
    delivery.finished_at, delivery.next_at = clock.now(), None
    record(session, delivery, "attempt_finished")
    await aggregate(session, event)
    await session.flush()


async def recover(session) -> None:
    rows = await session.scalars(
        select(EventDelivery)
        .where(
            EventDelivery.state == "processing", EventDelivery.started_at < clock.now() - timedelta(minutes=5)
        )
        .with_for_update(skip_locked=True)
        .limit(200)
    )
    for delivery in rows:
        delivery.state, delivery.error = "unknown", "WORKER_INTERRUPTED_REQUIRES_REVIEW"
        delivery.finished_at = clock.now()
        record(session, delivery, "attempt_interrupted")
        await aggregate(session, await session.get(Event, delivery.event_id))


async def due(session) -> list[int]:
    return list(
        await session.scalars(
            select(EventDelivery.id)
            .where(
                EventDelivery.state == "pending",
                or_(EventDelivery.next_at.is_(None), EventDelivery.next_at <= clock.now()),
            )
            .order_by(EventDelivery.next_at.asc().nulls_first(), EventDelivery.id)
            .limit(200)
        )
    )
