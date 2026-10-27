"""Ingress trust boundary: authenticate before parsing a bounded JSON envelope."""

import json
import math
import secrets
from typing import Annotated, Any

from fastapi import Header, Request
from pydantic import AwareDatetime, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import select

from app.common.ontology import DEFAULT_ONTOLOGY_ID, set_ontology_scope
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.schema import ApiModel
from app.db.session import SessionDep

MAX_BYTES = 65_536


def bad(message: str, code: str = "VALIDATION_FAILED", status: int = 422) -> AppError:
    return AppError(message, code=code, status_code=status)


def valid_json(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [0, 0]
    budget[0] += 1
    if depth > 16 or budget[0] > 2048:
        raise ValueError("事件 JSON 嵌套最多16层、总值最多2048项")
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("事件文本不能包含空字符")
        try:
            budget[1] += len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("事件文本含无效 Unicode 字符") from None
        if budget[1] > MAX_BYTES:
            raise ValueError("事件文本超过64 KiB")
    elif isinstance(value, list | dict):
        if len(value) > 512:
            raise ValueError("事件单个容器最多512项")
        if isinstance(value, dict):
            for key in value:
                if not isinstance(key, str):
                    raise ValueError("事件 JSON 对象键须为文本")
                valid_json(key, depth + 1, budget)
            values = value.values()
        else:
            values = value
        for item in values:
            valid_json(item, depth + 1, budget)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("事件数值必须有限")
    elif value is not None and not isinstance(value, bool | int | float):
        raise ValueError("事件只接受 JSON 数据")


class IngressIn(ApiModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    occurred_at: AwareDatetime
    source: str = Field(min_length=1, max_length=128)
    object_type: str = Field(min_length=1, max_length=64)
    object_id: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]
    dedup_key: str | None = Field(default=None, min_length=1, max_length=200)
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("event_id", "event_type", "source", "object_type", "object_id", "dedup_key", "trace_id")
    @classmethod
    def text(cls, value: str | None) -> str | None:
        if value is not None:
            valid_json(value)
            if not value.strip():
                raise ValueError("事件信封字段不能全为空白")
        return value

    @field_validator("payload")
    @classmethod
    def payload_json(cls, value: dict) -> dict:
        valid_json(value)
        return value


async def authenticate(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
    x_ontology_id: Annotated[str | None, Header()] = None,
) -> None:
    from app.modules.ontology.models import OntologyWorkspace

    if x_ontology_id is not None and (len(x_ontology_id) > 19 or not x_ontology_id.isascii() or not x_ontology_id.isdigit()):
        raise bad("本体工作空间编号无效")
    ontology_id = int(x_ontology_id) if x_ontology_id is not None else DEFAULT_ONTOLOGY_ID
    if ontology_id <= 0 or ontology_id > 2**63 - 1:
        raise bad("本体工作空间编号无效")
    settings = get_settings()
    configured = settings.internal_event_token
    secret = configured.get_secret_value() if configured and ontology_id == DEFAULT_ONTOLOGY_ID else ""
    if settings.internal_event_tokens:
        try:
            tokens = json.loads(settings.internal_event_tokens.get_secret_value())
            if not isinstance(tokens, dict) or any(
                not isinstance(key, str) or len(key) > 19 or not key.isascii() or not key.isdigit()
                or not 0 < int(key) <= 2**63 - 1 or str(int(key)) != key
                or not isinstance(value, str) or not value.strip()
                for key, value in tokens.items()
            ):
                raise ValueError
            secret = tokens.get(str(ontology_id), secret)
        except (ValueError, TypeError):
            raise bad("内部事件工作空间凭证配置无效", "EVENT_INGRESS_UNAVAILABLE", 503) from None
    if not secret:
        raise bad("该本体工作空间的内部事件凭证尚未配置", "EVENT_INGRESS_UNAVAILABLE", 503)
    parts = authorization.split(" ", 1) if authorization else []
    provided = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
    if not secrets.compare_digest(provided.encode("utf-8"), secret.encode("utf-8")):
        raise bad("内部事件凭证无效", "UNAUTHORIZED", 401)
    if await session.scalar(select(OntologyWorkspace.id).where(OntologyWorkspace.id == ontology_id)) is None:
        raise bad("本体工作空间不存在", "NOT_FOUND", 404)
    await set_ontology_scope(session, ontology_id)


async def read_envelope(request: Request) -> IngressIn:
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BYTES:
            raise bad("事件信封最多64 KiB", "EVENT_TOO_LARGE", 413)
        raw.extend(chunk)
    try:
        value = json.loads(raw.decode("utf-8"))
        valid_json(value)
        return IngressIn.model_validate(value)
    except ValidationError as exc:
        raise AppError(
            "事件信封校验失败",
            code="VALIDATION_FAILED",
            status_code=422,
            details={
                "fields": [
                    {"field": ".".join(map(str, e["loc"])), "message": e["msg"]}
                    for e in exc.errors(include_input=False, include_url=False)
                ]
            },
        ) from None
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise bad("事件信封须为合法且有界的 UTF-8 JSON 对象") from None
