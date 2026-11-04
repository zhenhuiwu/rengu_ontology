"""Explicit committed CDC facts to the existing transactional event inbox/outbox."""

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError
from sqlalchemy import select

from app.common.auth import check_func
from app.core.errors import AppError
from app.modules.action.models import TypeStatus
from app.modules.event.delivery import discover
from app.modules.event.ingress import receive
from app.modules.event.ingress_schema import IngressIn
from app.modules.event.models import EventSource, EventType
from app.modules.event.payload_schema import field_paths


class FieldMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=256)


class EventMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    event_type_id: str = Field(alias="eventTypeId", min_length=1, max_length=64)
    event_version: str = Field(alias="eventVersion", min_length=1, max_length=16)
    operations: list[str] = Field(min_length=1, max_length=3)
    fields: list[FieldMap] = Field(max_length=64)
    _field_types: dict = PrivateAttr(default_factory=dict)


def unavailable(message):
    return AppError(message, code="CDC_EVENT_MAPPING_INVALID", status_code=409)


def source_paths(columns):
    return [
        "op",
        "sourcePosition.file",
        "sourcePosition.position",
        "sourcePosition.gtid",
        "before",
        "after",
        "sourcePosition",
        *[f"{side}.{column}" for side in ("before", "after") for column in columns],
    ]


def validate_mapping(mapping, event_type, object_code, columns):
    if (
        event_type is None
        or event_type.status != TypeStatus.published
        or event_type.retired_release_id is not None
        or event_type.source != EventSource.sync
        or event_type.obj_kind != object_code
        or event_type.version != mapping.event_version
    ):
        raise unavailable("须重新选择当前对象已发布的数据同步事件及固定版本")
    targets = field_paths(event_type.payload)
    mapping._field_types = {
        path: kind for path, (_, kind) in field_paths(event_type.payload, typed=True).items()
    }
    names = [item.target for item in mapping.fields]
    if (
        len(set(names)) != len(names)
        or any(a != b and b.startswith(a + ".") for a in names for b in names)
        or not set(mapping.operations) <= {"insert", "update", "delete"}
        or any(
            item.target not in targets or item.source not in source_paths(columns) for item in mapping.fields
        )
    ):
        raise unavailable("事件映射含未声明字段、重叠字段或未授权源列")


def mapped_value(mapping, event, target, source, value):
    if isinstance(value, dict):
        return {
            key: mapped_value(mapping, event, target + "." + key, source + "." + key, child)
            for key, child in value.items()
        }
    kind = mapping._field_types.get(target)
    if source not in event["position"].get("decimalPaths", []) or value is None:
        return value
    if kind not in ("整数", "金额", "百分比", "数值", "小数"):
        return value  # Exact source decimal text remains available for explicitly textual payload fields.
    decimal = Decimal(value)
    if decimal.is_finite() and decimal == decimal.to_integral_value():
        return int(decimal)
    if kind != "整数" and decimal.is_finite():
        number = float(decimal)
        if Decimal(json.dumps(number)) == decimal:
            return number  # Only when the JSON numeric lexeme preserves the original decimal exactly.
    raise unavailable("数据库小数无法无损映射为事件数值；请调整事件字段类型，不会截断或推进游标")


def envelope(mapping, event, *, ontology_id, object_code, primary):
    """Only source-typed decimals get a precision-checked JSON number; strings are never guessed."""
    if event["op"] not in mapping.operations:
        return None
    values = {
        "before": event["before"],
        "after": event["after"],
        "op": event["op"],
        "sourcePosition": event["position"],
    }
    payload = {}
    for item in mapping.fields:
        value = values
        for part in item.source.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        value = mapped_value(mapping, event, item.target, item.source, value)
        target = payload
        parts = item.target.split(".")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    image = event["after"] if event["after"] is not None else event["before"]
    digest = hashlib.sha256(f"{ontology_id}:{object_code}:{event['eventKey']}".encode()).hexdigest()
    return IngressIn(
        eventId="CDC-" + digest[:60],
        eventType=mapping.event_type_id,
        occurredAt=datetime.fromisoformat(event["at"]),
        source="mysql-binlog",
        objectType=object_code,
        objectId=str(image[primary]),
        payload=payload,
        dedupKey="CDC-" + digest[:60],
    )


async def prepare(session, user, sub):
    raw = (sub.cursor_value or {}).get("eventMapping")
    if raw is None:
        return None
    check_func(user, "event-types", "manage")
    mapping = EventMapping.model_validate(raw)
    event_type = await session.scalar(
        select(EventType).where(EventType.code == mapping.event_type_id).with_for_update(read=True)
    )
    validate_mapping(mapping, event_type, sub.object_code, sub.capture["readColumns"])
    return mapping


async def emit(session, sub, mapping, event):
    if mapping is None:
        return
    try:
        body = envelope(
            mapping,
            event,
            ontology_id=sub.ontology_id,
            object_code=sub.object_code,
            primary=sub.capture["primaryKey"],
        )
        if body is not None:
            # Retain inbox + immutable subscription outbox in the same cursor/fact transaction.
            await receive(session, body)
            await discover(session, body.event_id)
    except AppError as exc:
        raise unavailable("CDC事件入站被拒绝：" + exc.message) from None
    except ValidationError:
        raise unavailable("CDC事实不符合事件信封大小、身份或JSON合同；未推进游标") from None


async def options(session, sub):
    event_types = list(
        await session.scalars(
            select(EventType).where(
                EventType.obj_kind == sub.object_code,
                EventType.source == EventSource.sync,
                EventType.status == TypeStatus.published,
                EventType.retired_release_id.is_(None),
            )
        )
    )
    return {
        "mapping": (sub.cursor_value or {}).get("eventMapping"),
        "mappingVersion": (sub.cursor_value or {}).get("eventMappingVersion", 0),
        "sourcePaths": source_paths(sub.capture["readColumns"]),
        "eventTypes": [
            {
                "id": item.code,
                "name": item.name,
                "version": item.version,
                "fields": [{"path": path, "name": name} for path, name in field_paths(item.payload).items()],
            }
            for item in event_types
        ],
    }
