"""Explicitly configured HTTP probes and idempotency queries. No implicit mock responses."""

import json
from urllib.parse import urlsplit

import httpx
from pydantic import ConfigDict, Field, SecretStr, ValidationError, field_validator

from app.contracts.actions import ExtStatus, KeyQueryResult
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.schema import ApiModel
from app.modules.integration.models import ExtSystem, ExtSystemStatus


def unavailable(message="未配置外部系统真实查询适配器") -> AppError:
    return AppError(message, code="RECONCILE_UNAVAILABLE", status_code=503)


class AdapterConfig(ApiModel):
    model_config = ConfigDict(extra="forbid")
    query_path: str | None = Field(default=None, max_length=255)
    probe_path: str | None = Field(default=None, max_length=255)
    submit_path: str | None = Field(default=None, max_length=255)
    actions: list[str] = Field(default_factory=list, max_length=100)
    token: SecretStr | None = None

    @field_validator("token")
    @classmethod
    def bearer_token(cls, value):
        if value is not None and any(ord(c) < 32 or ord(c) == 127 for c in value.get_secret_value()):
            raise ValueError("Bearer token cannot contain control characters")
        return value

    @field_validator("query_path", "probe_path", "submit_path")
    @classmethod
    def relative_path(cls, value):
        if value is not None:
            parts = urlsplit(value)
            if (
                not value.startswith("/")
                or value.startswith("//")
                or parts.netloc
                or parts.scheme
                or parts.query
                or parts.fragment
                or "\\" in value
            ):
                raise ValueError("Only an absolute path on the configured system origin is allowed")
            if any(ord(c) < 32 for c in value):
                raise ValueError("Control characters are not allowed")
        return value


class QueryResponse(ApiModel):
    model_config = ConfigDict(extra="forbid")
    ext_status: ExtStatus
    ext_ref: str | None = Field(default=None, max_length=128)

    @field_validator("ext_ref")
    @classmethod
    def receipt_text(cls, value):
        if value is not None and any(ord(c) < 32 for c in value):
            raise ValueError("Receipt cannot contain control characters")
        return value


class HttpAdapter:
    def __init__(self, system: ExtSystem, config: AdapterConfig):
        self.system, self.config = system, config
        self.last_http: int | None = None
        endpoint = urlsplit(system.endpoint)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise unavailable("外部系统地址须为不含凭据或查询参数的 HTTPS 地址")
        self.origin = f"{endpoint.scheme}://{endpoint.netloc}"

    def path(self, operation: str) -> str:
        value = {"query": self.config.query_path, "probe": self.config.probe_path,
                 "submit": self.config.submit_path}.get(operation)
        if not value:
            raise unavailable(
                {"query": "该外部系统未配置幂等查询路径",
                 "probe": "该外部系统未配置只读连通性探测路径",
                 "submit": "该外部系统未配置真实动作执行路径"}.get(operation, "适配器操作无效")
            )
        return value

    async def request(self, operation: str, key: str | None = None) -> httpx.Response:
        path = self.path(operation)
        url = f"{self.origin}{path}"
        headers = {"Accept": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token.get_secret_value()}"
        try:
            async with httpx.AsyncClient(
                timeout=max(0.1, min(self.system.timeout_ms / 1000, 60)),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(
                    url, params={"idemKey": key} if key is not None else None, headers=headers
                )
            self.last_http = response.status_code
            if not 200 <= response.status_code < 300:
                raise AppError("外部查询或探测未返回成功状态", code="EXT_QUERY_FAILED", status_code=503)
            return response
        except httpx.HTTPError:
            raise AppError("外部查询或探测超时或连接失败", code="EXT_QUERY_FAILED", status_code=503) from None

    async def query_by_key(self, idempotency_key: str) -> KeyQueryResult:
        response = await self.request("query", idempotency_key)
        try:
            if len(response.content) > 65_536:
                raise ValueError
            result = QueryResponse.model_validate(response.json())
        except (ValidationError, ValueError):
            raise AppError(
                "外部幂等查询响应不符合约定，不能推断执行结果",
                code="EXT_QUERY_INVALID_RESPONSE",
                status_code=503,
            ) from None
        return KeyQueryResult(result.ext_status, result.ext_ref)


def get_adapter(system: ExtSystem, operation: str = "query") -> HttpAdapter:
    settings = get_settings()
    if system.status == ExtSystemStatus.disabled:
        raise unavailable("外部系统已停用，不能自动查询")
    if not settings.integration_real_connect or settings.integration_adapters is None:
        raise unavailable(
            "真实外部调用未启用或未配置；请配置 RENGU_INTEGRATION_REAL_CONNECT 与 RENGU_INTEGRATION_ADAPTERS"
        )
    try:
        mapping = json.loads(settings.integration_adapters.get_secret_value())
        if not isinstance(mapping, dict) or not isinstance(mapping.get(system.code), dict):
            raise ValueError
        config = AdapterConfig.model_validate(mapping[system.code])
    except (ValueError, TypeError, ValidationError):
        raise unavailable("该系统的适配器配置缺失或格式无效") from None
    adapter = HttpAdapter(system, config)
    adapter.path(operation)
    return adapter
