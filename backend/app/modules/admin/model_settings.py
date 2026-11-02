"""Shared model configuration. Saving never calls a model or returns reversible credentials."""

import json
import os
import re
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field, SecretStr, TypeAdapter, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.common.auth import CurrentUser, require_func
from app.core.config import get_settings
from app.core.errors import AppError, VersionConflict
from app.core.schema import ApiModel, DateTimeMin
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.admin.models import PlatformSetting
from app.modules.admin.repo import insert_audit
from app.modules.data.secrets import decrypt_password, encrypt_password

Usage = Literal["qa", "ontology"]
LABELS = {"qa": "问数解析", "ontology": "本体生成润色"}
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"
MODEL_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$"
router = APIRouter(prefix="/settings/models", tags=["admin"])
ViewUser = Annotated[CurrentUser, Depends(require_func("settings", "view"))]
ManageUser = Annotated[CurrentUser, Depends(require_func("settings", "manage"))]


class ModelValue(ApiModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "ollama"] = "openai"
    model: str = Field(pattern=MODEL_PATTERN)
    base_url: str = Field(min_length=1, max_length=1000)
    timeout_sec: float = Field(ge=5, le=300, allow_inf_nan=False, strict=True)
    enabled: bool = Field(strict=True)
    auth_mode: Literal["key", "none"] = "key"

    @model_validator(mode="before")
    @classmethod
    def ollama_defaults(cls, value):
        if isinstance(value, dict) and value.get("provider") == "ollama":
            value = dict(value)
            if "baseUrl" not in value and "base_url" not in value:
                value["base_url"] = DEFAULT_OLLAMA_BASE_URL
            if "authMode" not in value and "auth_mode" not in value:
                value["auth_mode"] = "none"
        return value

    @model_validator(mode="after")
    def compatible_url(self):
        if self.provider == "ollama" and not self.base_url.endswith("/v1"):
            self.base_url += "/v1"
        return self

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        try:
            url = urlsplit(value)
            port = url.port
        except ValueError:
            raise ValueError("模型地址格式无效") from None
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value
            or port == 0
        ):
            raise ValueError("模型地址格式无效，请填写 HTTP(S) Base URL")
        if "@" in url.netloc or url.username or url.password:
            raise ValueError("模型地址不能包含凭据，请填写密钥字段")
        if "?" in value or url.query:
            raise ValueError("模型地址暂不支持查询参数，请移除后重试")
        if "#" in value or url.fragment:
            raise ValueError("地址片段不会发送给模型服务，请移除后重试")
        return value


class ModelConnectionIn(ModelValue):
    model: str | None = Field(default=None, pattern=r"^(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127})?$")
    api_key: SecretStr | None = None  # Omitted means keep; an empty string is never a delete command.

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not raw or len(raw) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
                raise ValueError("密钥不能为空，且须为不含空白的有效密钥")
        return value


class ModelSaveIn(ModelConnectionIn):
    model: str = Field(pattern=MODEL_PATTERN)
    lock_version: int = Field(ge=0, strict=True)


@dataclass(frozen=True)
class ModelRuntime:
    value: ModelValue
    api_key: SecretStr
    source: str
    key_source: str
    lock_version: int = 0
    updated_at: str | None = None


def environment_model(usage: Usage) -> ModelRuntime:
    settings = get_settings()
    qa = usage == "qa"
    prefix = "QA" if qa else "AUTO_ONTOLOGY"
    provider = os.environ.get(f"{prefix}_LLM_PROVIDER") or (
        settings.qa_llm_provider if qa else settings.auto_llm_provider
    )
    key = os.environ.get(
        "DASHSCOPE_API_KEY", settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    ).strip()
    if provider == "ollama":
        key = ""  # A local provider must never inherit the shared cloud credential.
    value = ModelValue(
        provider=provider,
        model=os.environ.get(f"{prefix}_LLM_MODEL")
        or (settings.qa_llm_model if qa else settings.auto_llm_model)
        or ("qwen3:8b" if provider == "ollama" else "qwen-flash"),
        base_url=os.environ.get(f"{prefix}_LLM_BASE_URL")
        or (settings.qa_llm_base_url if qa else settings.auto_llm_base_url)
        or (DEFAULT_OLLAMA_BASE_URL if provider == "ollama" else DEFAULT_BASE_URL),
        timeout_sec=float(
            os.environ.get(f"{prefix}_LLM_TIMEOUT_S")
            or (settings.qa_llm_timeout_s if qa else settings.auto_llm_timeout_s)
            or (30 if qa else 60)
        ),
        enabled=provider == "ollama" or bool(key),
        auth_mode="none" if provider == "ollama" else "key",
    )
    return ModelRuntime(value, SecretStr(key), "environment", "environment" if key else "none")


def origin(value: str) -> str:
    url = urlsplit(value)
    host = f"[{url.hostname}]" if ":" in (url.hostname or "") else url.hostname
    return f"{url.scheme}://{host}:{url.port or (443 if url.scheme == 'https' else 80)}"


def same_key_source(value: ModelValue, previous: ModelValue) -> bool:
    return (
        value.provider == previous.provider
        and origin(value.base_url) == origin(previous.base_url)
        and previous.auth_mode == "key"
    )


async def load_model_runtime(
    session: AsyncSession, usage: Usage, *, decrypt_key: bool = True
) -> ModelRuntime:
    row = await session.scalar(
        select(PlatformSetting)
        .where(PlatformSetting.code == f"model:{usage}")
        .execution_options(populate_existing=True)
    )
    env = environment_model(usage)
    if row is None:
        return env
    value = ModelValue.model_validate(row.value)
    env_allowed = same_key_source(value, env.value)
    key = env.api_key.get_secret_value() if value.auth_mode == "key" and env_allowed else ""
    if row.secret_enc and value.auth_mode == "key":
        key = decrypt_password(row.secret_enc) if decrypt_key else ""
    if decrypt_key and value.auth_mode == "key" and row.secret_enc and key is None:
        raise AppError(
            "已保存的模型密钥无法解密，请更换密钥", code="MODEL_SECRET_UNAVAILABLE", status_code=503
        )
    return ModelRuntime(
        value,
        SecretStr(key or ""),
        "database",
        ("database" if row.secret_enc else env.key_source if env_allowed else "none")
        if value.auth_mode == "key"
        else "none",
        row.lock_version,
        TypeAdapter(DateTimeMin).dump_python(row.updated_at, mode="json"),
    )


def public_model(usage: Usage, runtime: ModelRuntime) -> dict:
    return {
        "usage": usage,
        "label": LABELS[usage],
        **runtime.value.model_dump(by_alias=True),
        "hasKey": runtime.key_source != "none",
        "source": runtime.source,
        "keySource": runtime.key_source,
        "lockVersion": runtime.lock_version,
        "updatedAt": runtime.updated_at,
    }


@router.get("")
async def model_settings(session: SessionDep, user: ViewUser) -> dict:
    return {
        "items": [
            public_model(usage, await load_model_runtime(session, usage, decrypt_key=False))
            for usage in LABELS
        ],
        "canManage": user.func.get("settings") == "manage",
    }


async def connection_key(session, usage: Usage, body: ModelConnectionIn) -> str:
    if body.auth_mode == "none":
        if body.api_key is not None:
            raise AppError("无鉴权模式不能同时提交密钥", code="VALIDATION_FAILED", status_code=422)
        return ""
    if body.api_key is not None:
        return body.api_key.get_secret_value()
    saved = await load_model_runtime(session, usage, decrypt_key=False)
    if not same_key_source(body, saved.value):
        raise AppError(
            "更换模型提供方、源站或启用密钥认证时，请填写新密钥",
            code="MODEL_SECRET_REQUIRED",
            status_code=422,
        )
    key = (await load_model_runtime(session, usage)).api_key.get_secret_value()
    if not key:
        raise AppError("检测密钥认证服务前请填写密钥", code="MODEL_SECRET_REQUIRED", status_code=422)
    return key


@router.post("/{usage}/connection")
async def model_connection(
    usage: Usage, body: ModelConnectionIn, session: SessionDep, user: ManageUser
) -> dict:
    """Read the draft endpoint's model directory; never save, infer, pull or follow redirects."""
    key = await connection_key(session, usage, body)
    result = {
        "ok": False,
        "provider": body.provider,
        "baseUrl": body.base_url,
        "modelOptions": [],
        "selectedModelAvailable": None,
        "error": None,
    }
    try:
        async with (
            httpx.AsyncClient(
                timeout=min(body.timeout_sec, 30), follow_redirects=False, verify=True
            ) as client,
            client.stream(
                "GET", f"{body.base_url}/models", headers={"Authorization": f"Bearer {key}"} if key else {}
            ) as response,
        ):
            response.raise_for_status()
            content = bytearray()
            async for part in response.aiter_bytes():
                content.extend(part)
                if len(content) > 1_048_576:
                    raise ValueError("directory too large")
        payload = json.loads(content)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(rows, list)
            or len(rows) > 10000
            or any(
                not isinstance(row, dict)
                or not isinstance(row.get("id"), str)
                or not re.fullmatch(MODEL_PATTERN, row["id"])
                for row in rows
            )
        ):
            raise ValueError("invalid model directory")
        models = sorted({row["id"] for row in rows})
        result.update(
            ok=True,
            modelOptions=[{"value": model, "label": model} for model in models],
            selectedModelAvailable=body.model in models if body.model else None,
        )
    except httpx.HTTPStatusError as error:
        result["error"] = (
            f"模型目录返回 HTTP {error.response.status_code}，请核对地址、认证及服务是否支持模型列表"
        )
    except httpx.TimeoutException:
        result["error"] = "读取模型目录超时，请核对服务状态和超时设置"
    except httpx.HTTPError:
        result["error"] = "无法连接模型目录，请核对服务地址、网络及 TLS 证书"
    except (ValueError, UnicodeError):
        result["error"] = "服务返回的模型目录格式无效或过大"
    return result


@router.put("/{usage}")
async def save_model_settings(
    usage: Usage, body: ModelSaveIn, session: SessionDep, user: ManageUser, request: Request
) -> dict:
    await get_adapter(session).xact_lock(session, f"platform:model:{usage}")
    row = await session.scalar(
        select(PlatformSetting)
        .where(PlatformSetting.code == f"model:{usage}")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    actual = row.lock_version if row is not None else 0
    if actual != body.lock_version:
        raise VersionConflict(details={"expected": body.lock_version, "actual": actual})
    previous = ModelValue.model_validate(row.value) if row else environment_model(usage).value
    if body.auth_mode == "key" and body.api_key is None and not same_key_source(body, previous):
        raise AppError(
            "更换模型提供方、源站或启用密钥认证时，请填写该服务的新密钥",
            code="MODEL_SECRET_REQUIRED",
            status_code=422,
        )
    if body.auth_mode == "none" and body.api_key is not None:
        raise AppError("无鉴权模式不能同时提交密钥", code="VALIDATION_FAILED", status_code=422)
    if (
        body.enabled
        and body.auth_mode == "key"
        and body.api_key is None
        and not (row and row.secret_enc)
        and not (
            same_key_source(body, environment_model(usage).value)
            and environment_model(usage).api_key.get_secret_value()
        )
    ):
        raise AppError("启用模型前请配置密钥", code="MODEL_SECRET_REQUIRED", status_code=422)
    value = ModelValue.model_validate(body.model_dump(include=set(ModelValue.model_fields)))
    if row is None:
        row = PlatformSetting(code=f"model:{usage}", value=value.model_dump(), created_by=user.id)
        session.add(row)
    else:
        row.value = value.model_dump()
        flag_modified(row, "value")
    if body.auth_mode == "none":
        row.secret_enc = None
    elif body.api_key is not None:
        row.secret_enc = encrypt_password(body.api_key.get_secret_value())
    await session.flush()
    auth_label = (
        "无鉴权" if body.auth_mode == "none" else "密钥更换" if body.api_key is not None else "密钥保留"
    )
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="修改模型配置",
        target=(
            f"{LABELS[usage]}；提供方 {value.provider}；模型 {value.model}；"
            f"{'启用' if value.enabled else '停用'}；认证{auth_label}"
        ),
        ip=(request.client.host if request.client else "")[:64],
        created_by=user.id,
    )
    return public_model(usage, await load_model_runtime(session, usage, decrypt_key=False))
