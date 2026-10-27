"""Transactional side effects; each external send is claimed and committed before the POST.

Retryable outcomes (transport error/timeout, 429, 5xx) are rescheduled on the same outbox row via
`available_at` with exponential backoff, bounded by the system's `max_retry` (≤5), reusing the same
Idempotency-Key. Every send is appended to `payload.attempts`. A crash after the claim never resends.
"""

import hashlib
import logging
import time

import httpx
from sqlalchemy import or_, select

from app.common.outbox import OutboxEvent, emit
from app.core import clock
from app.core.errors import AppError
from app.jobs.scope import scoped_sessions
from app.modules.action.checker import structured_params
from app.modules.action.models import TypeStatus
from app.modules.dynamic.models import ActionRun
from app.modules.event.ingress import receive, validate_payload
from app.modules.event.ingress_schema import IngressIn
from app.modules.event.models import Event, EventType
from app.modules.integration.action_edits import (
    existing_row,
    mapping_json,
    object_binding,
    object_operation,
    unavailable,
    value_source,
)
from app.modules.integration.adapters import get_adapter
from app.modules.integration.models import ExtSystem
from app.modules.integration.signing import (
    SigningSecretInvalid,
    backoff,
    encode_body,
    resolve_secret,
    retry_budget,
    retryable_status,
    signed_headers,
)
from app.modules.machine.principal import actor_of, resolve_actor

TOPIC = "action.effect"
RETRY_SCHEDULED = "EFFECT_RETRY_SCHEDULED"
CLAIMED = "CLAIMED_OUTCOME_UNKNOWN"
STANDARD = {"ActionExecuted", "ActionOutcomeUnknown"}
logger = logging.getLogger(__name__)


def effect_timing(effect):
    when = effect.get("when", "")
    if not isinstance(when, str):
        raise unavailable("副作用时机须为明确选项")
    when = when.strip()
    if when in ("写回成功后", "执行成功后", "成功后", "ActionExecuted"):
        return "ActionExecuted"
    if when in ("结果未知时", "结果未知后", "ActionOutcomeUnknown"):
        return "ActionOutcomeUnknown"
    raise unavailable("副作用时机须明确选择执行成功后或结果未知时")


async def external_binding(session, definition, target):
    if not isinstance(target, str) or ":" not in target:
        raise unavailable("通知/Webhook须选择已配置的系统与投递目标")
    code, key = target.split(":", 1)
    system = await session.scalar(select(ExtSystem).where(ExtSystem.code == code))
    if system is None:
        raise unavailable("副作用外部系统不存在")
    adapter = get_adapter(system, "effect")
    if definition["id"] not in adapter.config.actions or not system.idempotent or system.idem_header.lower() != "idempotency-key":
        raise unavailable("副作用要求显式动作授权及外部幂等支持")
    path = adapter.config.effect_paths.get(key)
    if not path:
        raise unavailable("副作用目标未配置真实投递路径")
    return system, adapter, f"{adapter.origin}{path}"


async def prepare_effect(session, user, run, definition, effect):
    if effect["kind"] == "事件" and effect["target"].split(" ", 1)[0] in STANDARD:
        return None
    when = effect_timing(effect)
    spec = mapping_json(effect.get("note"))
    payload_spec = spec.get("payload")
    if not isinstance(payload_spec, dict) or len(payload_spec) > 64:
        raise unavailable("副作用须明确填写载荷字段映射")
    params = structured_params(run.params if run.params is not None else run.params_text)
    payload = {key: await value_source(session, user, source, params) for key, source in payload_spec.items()}
    from app.modules.functions.runtime import json_value
    json_value(payload)
    if effect["kind"] in ("通知", "Webhook"):
        if set(spec) != {"payload"}:
            raise unavailable("通知/Webhook只接受明确载荷映射")
        _, _, url = await external_binding(session, definition, effect["target"])
        return {"when": when, "payload": payload, "url": url}
    if effect["kind"] != "事件" or set(spec) != {"payload", "version", "object"}:
        raise unavailable("自定义事件须绑定版本、对象与载荷映射")
    event = await session.scalar(select(EventType).where(EventType.code == effect["target"])
                                .with_for_update(read=True))
    if (event is None or event.retired_release_id is not None or event.status != TypeStatus.published
            or event.version != spec["version"]):
        raise unavailable("自定义事件绑定的已发布版本不可用")
    obj, governed = await object_binding(session, event.obj_kind)
    await object_operation(session, user, obj, definition)
    from app.modules.integration.action_edits import mapped_target
    code = mapped_target({}, {"target": spec["object"]}, run, definition, obj, params)
    await existing_row(session, user, obj, governed, code)
    validate_payload(event, payload)
    if set(payload) - {p.api for p in event.payload}:
        raise unavailable("自定义事件载荷包含未声明字段")
    return {"when": when, "payload": payload, "objectType": event.obj_kind, "objectId": code,
            "version": event.version}


async def local_effects(session, definition):
    """Effects executed locally; natural-language ones on an external-Webhook action are declared only."""
    from app.modules.integration.action_edits import declared_text, external_webhook
    effects = definition["writeback"]["effects"]
    if not await external_webhook(session, definition):
        return list(enumerate(effects))
    return [(i, e) for i, e in enumerate(effects)
            if (e.get("kind") == "事件" and str(e.get("target") or "").split(" ", 1)[0] in STANDARD)
            or not declared_text(e.get("note"))]


async def preflight_effects(session, user, run, definition):
    for _, effect in await local_effects(session, definition):
        await prepare_effect(session, user, run, definition, effect)


async def retain_effects(session, user, run, definition, event_type):
    for index, effect in await local_effects(session, definition):
        data = await prepare_effect(session, user, run, definition, effect)
        if data is None or data["when"] != event_type:
            continue
        source = f"{run.code}:{run.type_version}:{index}"
        if run.ontology_id != 1:
            source = f"{run.ontology_id}:{source}"
        identity = "effect:" + hashlib.sha256(source.encode()).hexdigest()[:56]
        if effect["kind"] == "事件":
            previous = await session.scalar(select(Event).where(Event.code == identity))
            await receive(session, IngressIn(event_id=identity, event_type=effect["target"],
                occurred_at=previous.occurred_at if previous else clock.now(), source="action.effect",
                object_type=data["objectType"], object_id=data["objectId"], payload=data["payload"], dedup_key=identity))
        elif not await session.scalar(select(OutboxEvent.id).where(OutboxEvent.event_id == identity)):
            await emit(session, TOPIC, {"ontologyId": run.ontology_id, "runId": run.code,
                "typeVersion": run.type_version, "actor": actor_of(user), "effect": effect, "data": data,
                "state": "queued"}, event_id=identity)


async def recheck_effect(session, data: dict):
    """Recheck current authority and payload before every send; never replace retained values."""
    from app.modules.integration.action_edits import execution_definition, target_access
    run = await session.scalar(select(ActionRun).where(ActionRun.code == data["runId"]).with_for_update())
    user = await resolve_actor(session, data["actor"])
    definition = await execution_definition(session, run)
    await target_access(session, user, run, definition)
    if run.type_version != data["typeVersion"] or data["effect"] not in definition["writeback"]["effects"]:
        raise unavailable("副作用绑定定义已变化")
    system, adapter, url = await external_binding(session, definition, data["effect"]["target"])
    if url != data["data"]["url"]:
        raise unavailable("排队后的副作用地址配置已变化")
    prepared = await prepare_effect(session, user, run, definition, data["effect"])
    if prepared != data["data"]:
        raise unavailable("副作用映射或授权上下文已变化")
    return run, system, adapter, url


async def dispatch_effect(effect_id, ontology_id: int):
    sessions = scoped_sessions(ontology_id)
    async with sessions() as session, session.begin():
        row = await session.get(OutboxEvent, effect_id, with_for_update=True)
        if row is None or row.topic != TOPIC or row.dispatched_at is not None:
            return
        if (row.attempts and row.last_error != RETRY_SCHEDULED) or row.available_at > clock.now():
            return
        data = row.payload
        try:
            run, system, adapter, url = await recheck_effect(session, data)
            try:
                secret = resolve_secret(adapter.config.signing_secret)
            except SigningSecretInvalid as exc:
                raise AppError("副作用签名密钥不可用", code=str(exc), status_code=503) from None
        except AppError as exc:
            # After an earlier send the receiver may already hold the request: never report "rejected".
            row.last_error = exc.code
            row.attempts = row.attempts or 1
            row.payload = {**data, "state": "unknown" if data.get("attempts") else "rejected"}
            return
        attempt = row.attempts + 1
        row.attempts, row.last_error = attempt, CLAIMED
        row.payload = {**data, "state": "unknown"}
        retries = retry_budget(system.max_retry)
        key, payload = row.event_id, {"ontologyId": ontology_id, "actionRunId": run.code,
                                      "effectId": row.event_id, "kind": data["effect"]["kind"],
                                      "payload": data["data"]["payload"]}
    body = encode_body(payload)
    headers = {"Accept": "application/json", "Idempotency-Key": key, "X-Ontology-Id": str(ontology_id)}
    if adapter.config.token:
        headers["Authorization"] = f"Bearer {adapter.config.token.get_secret_value()}"
    headers = signed_headers(headers, body, secret)
    state, error, receipt, status, retry_after = "unknown", "EXTERNAL_OUTCOME_UNKNOWN", None, None, None
    retryable, started, timer = False, clock.now(), time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=max(.1, min(system.timeout_ms / 1000, 60)),
                                     follow_redirects=False, trust_env=False) as client:
            response = await client.post(url, content=body, headers=headers)
        status = response.status_code
        if retryable_status(status):
            retryable, state, error = True, "failed", f"EXTERNAL_HTTP_{status}"
            retry_after = response.headers.get("Retry-After")
        elif not 200 <= status < 300:
            state, error = "failed", f"EXTERNAL_HTTP_{status}"
        elif len(response.content) <= 65_536:
            from app.modules.integration.execution import ExecutionResponse
            result = ExecutionResponse.model_validate(response.json())
            if result.status.value == "SUCCESS" and result.receipt_id and result.receipt_id.strip():
                state, error, receipt = "succeeded", None, result.receipt_id
            elif result.status.value == "FAILED":
                state, error = "failed", result.code or "EXTERNAL_REJECTED"
    except httpx.TransportError:
        retryable = True
    except (httpx.HTTPError, ValueError):
        pass
    elapsed = round((time.perf_counter() - timer) * 1000)
    async with sessions() as session, session.begin():
        row = await session.get(OutboxEvent, effect_id, with_for_update=True)
        history = [*row.payload.get("attempts", []), {
            "attempt": attempt, "at": started.isoformat(), "httpStatus": status, "error": error,
            "retryable": retryable, "signed": secret is not None, "elapsedMs": elapsed}]
        if retryable and attempt <= retries:
            row.payload = {**row.payload, "state": "retrying", "httpStatus": status, "receiptId": None,
                           "attempts": history}
            row.last_error = RETRY_SCHEDULED
            row.available_at = clock.now() + backoff(attempt, retry_after)
            return
        row.payload = {**row.payload, "state": state, "httpStatus": status, "receiptId": receipt,
                       "attempts": history}
        row.last_error = error
        if state == "succeeded":
            row.dispatched_at = clock.now()


async def dispatch_effects(ontology_id: int):
    async with scoped_sessions(ontology_id)() as session:
        ids = list(await session.scalars(select(OutboxEvent.id).where(OutboxEvent.topic == TOPIC,
            or_(OutboxEvent.attempts == 0, OutboxEvent.last_error == RETRY_SCHEDULED),
            OutboxEvent.dispatched_at.is_(None), OutboxEvent.available_at <= clock.now())
            .order_by(OutboxEvent.id).limit(100)))
    for identity in ids:
        try:
            await dispatch_effect(identity, ontology_id)
        except Exception:
            logger.exception("Effect dispatch failed for outbox %s", identity)
    return len(ids)
