"""Validate definitions and optionally enqueue an explicitly configured isolated HTTP test channel."""

import io
import json
import tokenize
import uuid
from typing import Any

from pydantic import ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from app.common.auth import CurrentUser, check_func
from app.common.context import ViewContext
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.schema import ApiModel
from app.modules.action.condition_expr import Unavailable, evaluate
from app.modules.action.models import TypeStatus
from app.modules.event.ingress import validate_fields
from app.modules.event.ingress_schema import valid_json
from app.modules.event.models import Event, EventOutcome, EventType
from app.modules.event.schemas import FieldOut, SubscriberOut
from app.modules.event.service import EventTypeService
from app.modules.static.repo import definition_draft_items
from app.modules.versioning.repo import ReleaseRepo


class SimulateIn(ApiModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any] | None = Field(default=None, max_length=512)

    @field_validator("payload")
    @classmethod
    def bounded_payload(cls, value):
        valid_json(value)
        return value


class FilterCheck(ApiModel):
    name: str
    matched: bool | None
    message: str


class SimulateOut(ApiModel):
    test_event_id: str | None = None
    delivered_to: list[str] = Field(default_factory=list)
    payload_valid: bool
    checks: list[FilterCheck]
    message: str = "载荷校验完成；未创建事件，未向订阅方投递"


def sample_value(field: FieldOut) -> Any:
    if field.type in ("整数", "金额", "百分比", "数值", "小数", "布尔"):
        try:
            return json.loads(field.sample)
        except (ValueError, TypeError):
            return field.sample  # Validation reports a bad sample rather than silently inventing one.
    return field.sample


def filter_matches(text: str, fields: list[FieldOut], payload: dict) -> bool:
    if text.strip() == "全部":
        return True
    if len(text) > 4096:
        raise Unavailable("过滤条件过长")
    names = {f.api: f.api for f in fields}
    for f in fields:
        if sum(row.name == f.name for row in fields) == 1:
            names.setdefault(f.name, f.api)
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
        rewritten = []
        for i, token in enumerate(tokens):
            value = token.string
            if token.type == tokenize.NAME and value in names and (not i or tokens[i - 1].string != "."):
                value = f"载荷.{names[value]}"
            rewritten.append((token.type, value))
        return evaluate(tokenize.untokenize(rewritten), {("载荷", k): v for k, v in payload.items()})
    except (tokenize.TokenError, IndentationError):
        raise Unavailable("过滤条件不是可执行表达式") from None


async def simulate(
    session, type_id: str, view: str, cr: str | None, body: SimulateIn, user: CurrentUser
) -> SimulateOut:
    check_func(user, "event-types", "manage")
    model = await session.scalar(
        select(EventType).where(EventType.code == type_id).with_for_update(read=True)
    )
    definition = await EventTypeService(session).one(ViewContext(), type_id)
    if definition["status"] == TypeStatus.draft.value:
        raise AppError("草稿事件类型尚未发布", code="TYPE_IS_DRAFT", status_code=409)
    if view == "draft":
        if not cr:
            raise AppError("草稿校验须指定变更单", code="VALIDATION_FAILED", status_code=422)
        for item in await definition_draft_items(session, cr, "event", type_id):
            section = {"payload": "payload", "subs": "subscribers"}.get(item["section"])
            if section is None:
                continue
            key = "api" if section == "payload" else "name"
            rows = definition[section]
            matches = [i for i, row in enumerate(rows) if row[key] == item["key"]]
            if len(matches) > 1:
                raise AppError("草稿键不唯一", code="VALIDATION_FAILED", status_code=422)
            if item["op"] == "delete":
                definition[section] = [row for row in rows if row[key] != item["key"]]
            elif item["op"] in ("add", "update") and isinstance(item["after"], dict):
                row = {**(rows[matches[0]] if matches else {}), **item["after"]}
                if row.get(key) != item["key"]:
                    raise AppError("草稿行键与定义不一致", code="VALIDATION_FAILED", status_code=422)
                if matches:
                    rows[matches[0]] = row
                else:
                    rows.append(row)
            else:
                raise AppError("草稿行格式无效", code="VALIDATION_FAILED", status_code=422)
    try:
        fields = [FieldOut.model_validate(row) for row in definition["payload"]]
        subscribers = [
            SubscriberOut.model_validate({**row, "weekly": 0}) for row in definition["subscribers"]
        ]
        if len({field.api for field in fields}) != len(fields):
            raise ValueError
    except (ValidationError, TypeError, ValueError):
        raise AppError(
            "事件定义格式无效，无法模拟校验", code="EVENT_DEFINITION_INVALID", status_code=503
        ) from None
    payload = body.payload if body.payload is not None else {f.api: sample_value(f) for f in fields}
    try:
        valid_json(payload)
    except ValueError as exc:
        raise AppError(str(exc), code="VALIDATION_FAILED", status_code=422) from None
    validate_fields(fields, payload)
    checks = []
    for subscriber in subscribers:
        if subscriber.status.value == "停用":
            continue
        try:
            matched = filter_matches(subscriber.filter, fields, payload)
            checks.append(
                FilterCheck(
                    name=subscriber.name,
                    matched=matched,
                    message="过滤条件匹配" if matched else "过滤条件未匹配",
                )
            )
        except Unavailable as exc:
            checks.append(FilterCheck(name=subscriber.name, matched=None, message=str(exc)))
    from app.modules.event.delivery import discover, http_target, routes

    configured = routes()
    enabled = {subscriber.name for subscriber in subscribers if subscriber.status.value == "启用"}
    has_test_channel = False
    if get_settings().event_delivery_http_enabled:
        for check in checks:
            route = configured.get(f"{type_id}/{check.name}", {})
            if (
                check.matched is not True
                or check.name not in enabled
                or route.get("action_type")
                or route.get("rule_code")
            ):
                continue
            try:
                http_target(route, True)
            except (Unavailable, ValueError, TypeError):
                continue
            has_test_channel = True
            break
    if not has_test_channel:
        return SimulateOut(
            payload_valid=True,
            checks=checks,
            message="载荷与过滤校验完成；没有匹配的已配置测试通道，未创建事件或投递",
        )
    event_id = f"TEST-EV-{uuid.uuid4().hex}"
    release = await ReleaseRepo(session).latest()
    session.add(
        Event(
            code=event_id,
            event_type_id=model.id,
            occurred_at=clock.now(),
            obj_kind=definition.get("objKind"),
            object_id="test-channel",
            obj_label="测试通道",
            summary="测试事件已排队，仅向显式配置的测试通道投递",
            outcome=EventOutcome.pending,
            subscriber_count=0,
            dedup_key=event_id,
            payload=payload,
            is_test=True,
            source="simulation",
            producer_type=type_id,
            producer_object_type=definition.get("objectApi"),
            ingested_at=clock.now(),
            release_id=release.id if release else None,
            definition_snapshot=definition,
            created_by=user.id,
        )
    )
    await session.flush()
    await discover(session)
    return SimulateOut(
        test_event_id=event_id,
        payload_valid=True,
        checks=checks,
        message="测试事件已排队，尚未投递；仅使用已配置测试通道，可刷新查看真实结果",
    )
