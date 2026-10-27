"""Shared model configuration. Saving never calls a model or returns reversible credentials."""

import os
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field, SecretStr, TypeAdapter, field_validator
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
router = APIRouter(prefix="/settings/models", tags=["admin"])
ViewUser = Annotated[CurrentUser, Depends(require_func("settings", "view"))]
ManageUser = Annotated[CurrentUser, Depends(require_func("settings", "manage"))]


class ModelValue(ApiModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,127}$")
    base_url: str = Field(min_length=1, max_length=1000)
    timeout_sec: float = Field(ge=5, le=300, allow_inf_nan=False, strict=True)
    enabled: bool = Field(strict=True)

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
            or url.username
            or url.password
            or url.query
            or url.fragment
            or any(c.isspace() or ord(c) < 32 for c in value)
            or "\\" in value
            or port == 0
        ):
            raise ValueError("模型地址须为无凭据、查询串或片段的 HTTP(S) Base URL")
        return value


class ModelSaveIn(ModelValue):
    lock_version: int = Field(ge=0, strict=True)
    api_key: SecretStr | None = None  # Omitted means keep; an empty string is never a delete command.

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not raw or len(raw) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in raw):
                raise ValueError("密钥不能为空，且须为不含空白的有效密钥")
        return value


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
    key = os.environ.get(
        "DASHSCOPE_API_KEY", settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
    ).strip()
    value = ModelValue(
        model=os.environ.get(f"{prefix}_LLM_MODEL")
        or (settings.qa_llm_model if qa else settings.auto_llm_model)
        or "qwen-flash",
        base_url=os.environ.get(f"{prefix}_LLM_BASE_URL")
        or (settings.qa_llm_base_url if qa else settings.auto_llm_base_url)
        or DEFAULT_BASE_URL,
        timeout_sec=float(
            os.environ.get(f"{prefix}_LLM_TIMEOUT_S")
            or (settings.qa_llm_timeout_s if qa else settings.auto_llm_timeout_s)
            or (30 if qa else 60)
        ),
        enabled=bool(key),
    )
    return ModelRuntime(value, SecretStr(key), "environment", "environment" if key else "none")


def origin(value: str) -> str:
    url = urlsplit(value)
    host = f"[{url.hostname}]" if ":" in (url.hostname or "") else url.hostname
    return f"{url.scheme}://{host}:{url.port or (443 if url.scheme == 'https' else 80)}"


def trusted_origins() -> set[str]:
    # Deployments authorize model origins; editing a path/model must not send a saved key to a new host.
    return {origin(environment_model(usage).value.base_url) for usage in LABELS}


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
    if decrypt_key and origin(value.base_url) not in trusted_origins():
        raise AppError("模型地址不在部署授权源站内", code="MODEL_ORIGIN_FORBIDDEN", status_code=503)
    key = env.api_key.get_secret_value()
    if row.secret_enc:
        key = decrypt_password(row.secret_enc) if decrypt_key else ""
    if decrypt_key and row.secret_enc and key is None:
        raise AppError(
            "已保存的模型密钥无法解密，请更换密钥", code="MODEL_SECRET_UNAVAILABLE", status_code=503
        )
    return ModelRuntime(
        value,
        SecretStr(key or ""),
        "database",
        "database" if row.secret_enc else env.key_source,
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
        "trustedOrigins": sorted(trusted_origins()),
    }


@router.put("/{usage}")
async def save_model_settings(
    usage: Usage, body: ModelSaveIn, session: SessionDep, user: ManageUser, request: Request
) -> dict:
    if origin(body.base_url) not in trusted_origins():
        raise AppError("模型地址须使用部署已授权的源站", code="MODEL_ORIGIN_FORBIDDEN", status_code=422)
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
    if (
        body.enabled
        and body.api_key is None
        and not (row and row.secret_enc)
        and not environment_model(usage).api_key.get_secret_value()
    ):
        raise AppError("启用模型前请配置密钥", code="MODEL_SECRET_REQUIRED", status_code=422)
    value = ModelValue.model_validate(body.model_dump(include=set(ModelValue.model_fields)))
    if row is None:
        row = PlatformSetting(code=f"model:{usage}", value=value.model_dump(), created_by=user.id)
        session.add(row)
    else:
        row.value = value.model_dump()
        flag_modified(row, "value")
    if body.api_key is not None:
        row.secret_enc = encrypt_password(body.api_key.get_secret_value())
    await session.flush()
    await insert_audit(
        session,
        actor=f"{user.name}（{user.id}）",
        action="修改模型配置",
        target=(
            f"{LABELS[usage]}；模型 {value.model}；{'启用' if value.enabled else '停用'}；"
            f"密钥{'更换' if body.api_key is not None else '保留'}"
        ),
        ip=(request.client.host if request.client else "")[:64],
        created_by=user.id,
    )
    return public_model(usage, await load_model_runtime(session, usage, decrypt_key=False))
