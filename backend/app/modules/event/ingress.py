"""Authenticate and atomically retain incoming events with their accepted definition."""

import hashlib
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select

from app.core import clock
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.action.models import TypeStatus
from app.modules.event.ingress_schema import IngressIn, authenticate, bad, read_envelope
from app.modules.event.models import Event, EventDedup, EventOutcome, EventType
from app.modules.event.sections import event_def
from app.modules.versioning.repo import ReleaseRepo

router = APIRouter(tags=["internal-events"], dependencies=[Depends(authenticate)])


def dedup_window(text: str) -> timedelta | None:
    if re.fullmatch(r"永久(?:（[^）]*）)?", text.strip()):
        return None
    match = re.fullmatch(r"(\d+)\s*(秒|分钟|小时|天)(?:（[^）]*）)?", text.strip())
    if match:
        try:
            n = int(match[1])
            if n > 0:
                return timedelta(seconds=n * {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400}[match[2]])
        except OverflowError:
            pass
    raise bad("事件类型去重窗口配置不可执行", "EVENT_DEDUP_CONFIG_INVALID", 409)


def validate_payload(event_type: EventType, payload: dict[str, Any]) -> None:
    validate_fields(event_type.payload, payload)


def validate_fields(fields, payload: dict[str, Any]) -> None:
    """Shared validation for real ingress and read-only simulation definitions."""
    for field in fields:
        value = payload.get(field.api)
        if value is None:
            if field.required:
                raise bad(f"缺少必填载荷字段：{field.api}")
            continue
        kind = field.type
        valid = False
        if kind in ("字符串", "文本", "枚举"):
            valid = isinstance(value, str)
        elif kind == "整数":
            valid = type(value) is int
        elif kind in ("金额", "百分比", "数值", "小数"):
            valid = type(value) in (int, float) and (not isinstance(value, float) or math.isfinite(value))
        elif kind == "布尔":
            valid = type(value) is bool
        elif kind in ("日期", "日期时间"):
            if isinstance(value, str):
                try:
                    (date.fromisoformat if kind == "日期" else datetime.fromisoformat)(value)
                    valid = True
                except ValueError:
                    pass
        else:
            raise bad(f"载荷字段 {field.api} 的类型尚不支持校验", "EVENT_PAYLOAD_CONFIG_INVALID", 409)
        if not valid:
            raise bad(f"载荷字段 {field.api} 须符合类型：{kind}")


async def receive(session, body: IngressIn) -> dict:
    adapter = get_adapter(session)
    # Consistent lock order: producer ID, parent type row, business dedup key.
    await adapter.xact_lock(session, f"event.ingress.id:{body.event_id}")
    envelope = body.model_dump(mode="json", by_alias=True)
    envelope["occurredAt"] = body.occurred_at.astimezone(UTC).isoformat()
    digest = hashlib.sha256(
        json.dumps(
            envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    previous = await session.scalar(
        select(Event).where(Event.code == body.event_id).execution_options(populate_existing=True)
    )
    if previous is not None:
        if previous.envelope_hash != digest or previous.is_test:
            raise bad("eventId已存在且信封内容不一致", "EVENT_ID_CONFLICT", 409)
        return {"eventId": previous.code, "deduped": previous.outcome == EventOutcome.deduped}
    matches = list(
        await session.scalars(
            select(EventType.id).where(
                or_(EventType.code == body.event_type, EventType.api == body.event_type)
            )
        )
    )
    if not matches:
        raise bad("事件类型不存在", "NOT_FOUND", 404)
    if len(matches) != 1:
        raise bad("事件类型标识不唯一，请使用明确的类型编码", "EVENT_TYPE_AMBIGUOUS", 409)
    event_type = await session.scalar(
        select(EventType)
        .where(EventType.id == matches[0])
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    if event_type is None or body.event_type not in (event_type.code, event_type.api):
        raise bad("事件类型已变化，请重新提交", "EVENT_TYPE_CHANGED", 409)
    if event_type.status != TypeStatus.published or event_type.retired_release_id is not None:
        raise bad("事件类型尚未发布或已下线", "EVENT_TYPE_NOT_PUBLISHED", 409)
    if body.object_type not in (event_type.object_api, event_type.obj_kind):
        raise bad("objectType与事件类型绑定对象不一致")
    validate_payload(event_type, body.payload)
    key = body.dedup_key
    if key is None:
        if event_type.dedup_key_rule.strip() in ("", "事件 ID", "事件ID", "eventId"):
            key = body.event_id
        else:
            raise bad(
                "该事件类型的去重规则为说明文本，请生产方计算并显式传dedupKey", "EVENT_DEDUP_KEY_REQUIRED"
            )
    window = dedup_window(event_type.dedup_window)
    await adapter.xact_lock(session, f"event.ingress.key:{event_type.id}:{key}")
    # Read the real release while holding the type lock: publication cannot change its definition meanwhile.
    release = await ReleaseRepo(session).latest()
    now = clock.now()
    state = await session.scalar(
        select(EventDedup)
        .where(EventDedup.event_type_id == event_type.id, EventDedup.dedup_key == key)
        .execution_options(populate_existing=True)
    )
    duplicate = state is not None and (window is None or now - state.last_accepted_at < window)
    if not duplicate:
        if state is None:
            session.add(
                EventDedup(
                    event_type_id=event_type.id,
                    dedup_key=key,
                    last_accepted_at=now,
                    created_by="internal:event",
                )
            )
        else:
            state.last_accepted_at = now
    session.add(
        Event(
            code=body.event_id,
            event_type_id=event_type.id,
            occurred_at=body.occurred_at,
            obj_kind=event_type.obj_kind,
            object_id=body.object_id,
            obj_label=body.object_id,
            summary="入站事件已去重" if duplicate else "已接收，等待订阅投递",
            outcome=EventOutcome.deduped if duplicate else EventOutcome.pending,
            subscriber_count=0,
            dedup_key=key,
            latency_ms=None,
            payload=body.payload,
            source=body.source,
            producer_type=body.event_type,
            producer_object_type=body.object_type,
            trace_id=body.trace_id,
            envelope_hash=digest,
            ingested_at=now,
            definition_snapshot=event_def(event_type),
            release_id=release.id if release else None,
            is_test=False,
            created_by="internal:event",
        )
    )
    await session.flush()
    return {"eventId": body.event_id, "deduped": duplicate}


@router.post("/events", summary="认证接收生产方事件；持久化后由独立订阅投递器处理")
async def ingest_event(session: SessionDep, body: Annotated[IngressIn, Depends(read_envelope)]) -> dict:
    return await receive(session, body)
