"""Transactional side effects; external sends are claimed once after commit."""

import hashlib
import logging

import httpx
from sqlalchemy import select

from app.common.auth import resolve_user
from app.common.outbox import OutboxEvent, emit
from app.core import clock
from app.core.errors import AppError
from app.db.session import get_sessionmaker
from app.modules.action.checker import structured_params
from app.modules.action.models import TypeStatus
from app.modules.dynamic.models import ActionRun
from app.modules.event.ingress import receive, validate_payload
from app.modules.event.ingress_schema import IngressIn
from app.modules.event.models import Event, EventType
from app.modules.integration.action_edits import (
    existing_row, mapping_json, object_binding, object_operation, value_source, unavailable,
)
from app.modules.integration.adapters import get_adapter
from app.modules.integration.models import ExtSystem

TOPIC = "action.effect"
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


async def preflight_effects(session, user, run, definition):
    for effect in definition["writeback"]["effects"]:
        await prepare_effect(session, user, run, definition, effect)


async def retain_effects(session, user, run, definition, event_type):
    for index, effect in enumerate(definition["writeback"]["effects"]):
        data = await prepare_effect(session, user, run, definition, effect)
        if data is None or data["when"] != event_type:
            continue
        identity = "effect:" + hashlib.sha256(f"{run.code}:{run.type_version}:{index}".encode()).hexdigest()[:56]
        if effect["kind"] == "事件":
            previous = await session.scalar(select(Event).where(Event.code == identity))
            await receive(session, IngressIn(event_id=identity, event_type=effect["target"],
                occurred_at=previous.occurred_at if previous else clock.now(), source="action.effect",
                object_type=data["objectType"], object_id=data["objectId"], payload=data["payload"], dedup_key=identity))
        elif not await session.scalar(select(OutboxEvent.id).where(OutboxEvent.event_id == identity)):
            await emit(session, TOPIC, {"runId": run.code, "typeVersion": run.type_version,
                "actor": user.id, "effect": effect, "data": data, "state": "queued"}, event_id=identity)


async def dispatch_effect(effect_id):
    sessions = get_sessionmaker()
    async with sessions() as session, session.begin():
        row = await session.get(OutboxEvent, effect_id, with_for_update=True)
        if row is None or row.topic != TOPIC or row.attempts or row.dispatched_at is not None:
            return
        data = row.payload
        try:
            from app.modules.integration.action_edits import execution_definition, target_access
            run = await session.scalar(select(ActionRun).where(ActionRun.code == data["runId"]).with_for_update())
            user = await resolve_user(session, data["actor"])
            definition = await execution_definition(session, run)
            await target_access(session, user, run, definition)
            if run.type_version != data["typeVersion"] or data["effect"] not in definition["writeback"]["effects"]:
                raise unavailable("副作用绑定定义已变化")
            system, adapter, url = await external_binding(session, definition, data["effect"]["target"])
            if url != data["data"]["url"]:
                raise unavailable("排队后的副作用地址配置已变化")
            # Recheck current authority and payload; never replace retained values after enqueue.
            prepared = await prepare_effect(session, user, run, definition, data["effect"])
            if prepared != data["data"]:
                raise unavailable("副作用映射或授权上下文已变化")
        except AppError as exc:
            row.attempts, row.last_error = 1, exc.code
            row.payload = {**data, "state": "rejected"}
            return
        row.attempts, row.last_error = 1, "CLAIMED_OUTCOME_UNKNOWN"
        row.payload = {**data, "state": "unknown"}
        key, payload = row.event_id, {"actionRunId": run.code, "effectId": row.event_id,
                                     "kind": data["effect"]["kind"], "payload": data["data"]["payload"]}
    headers = {"Accept": "application/json", "Idempotency-Key": key}
    if adapter.config.token:
        headers["Authorization"] = f"Bearer {adapter.config.token.get_secret_value()}"
    state, error, receipt, status = "unknown", "EXTERNAL_OUTCOME_UNKNOWN", None, None
    try:
        async with httpx.AsyncClient(timeout=max(.1, min(system.timeout_ms / 1000, 60)),
                                     follow_redirects=False, trust_env=False) as client:
            response = await client.post(url, json=payload, headers=headers)
        status = response.status_code
        if 200 <= status < 300 and len(response.content) <= 65_536:
            from app.modules.integration.execution import ExecutionResponse
            result = ExecutionResponse.model_validate(response.json())
            if result.status.value == "SUCCESS" and result.receipt_id and result.receipt_id.strip():
                state, error, receipt = "succeeded", None, result.receipt_id
            elif result.status.value == "FAILED":
                state, error = "failed", result.code or "EXTERNAL_REJECTED"
    except (httpx.HTTPError, ValueError):
        pass
    async with sessions() as session, session.begin():
        row = await session.get(OutboxEvent, effect_id, with_for_update=True)
        row.payload = {**row.payload, "state": state, "httpStatus": status, "receiptId": receipt}
        row.last_error = error
        if state == "succeeded":
            row.dispatched_at = clock.now()


async def dispatch_effects():
    async with get_sessionmaker()() as session:
        ids = list(await session.scalars(select(OutboxEvent.id).where(OutboxEvent.topic == TOPIC,
            OutboxEvent.attempts == 0, OutboxEvent.dispatched_at.is_(None), OutboxEvent.available_at <= clock.now())
            .order_by(OutboxEvent.id).limit(100)))
    for identity in ids:
        try:
            await dispatch_effect(identity)
        except Exception:
            logger.exception("Effect dispatch failed for outbox %s", identity)
    return len(ids)
