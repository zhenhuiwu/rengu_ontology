"""Explicitly configured HTTP probes and idempotency queries. No implicit mock responses."""

import json
import ipaddress
import asyncio
import socket
import re
import ssl
from urllib.parse import urlsplit

import httpx
from pydantic import ConfigDict, Field, SecretStr, ValidationError, field_validator

from app.contracts.actions import ExtStatus, KeyQueryResult
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.schema import ApiModel
from app.modules.integration.models import NO_AUTH, ExtSystem, ExtSystemStatus


def unavailable(message="未配置外部系统真实查询适配器") -> AppError:
    return AppError(message, code="RECONCILE_UNAVAILABLE", status_code=503)


def forbidden_metadata_host(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    if host in {"metadata.google.internal", "metadata.goog", "instance-data.ec2.internal", "metadata.azure.internal"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_link_local or address in (ipaddress.ip_address("100.100.100.200"), ipaddress.ip_address("fd00:ec2::254"))


async def pin_http_target(url: str) -> tuple[str, dict, dict]:
    """Resolve once, reject metadata addresses, then connect to the checked IP."""
    try:
        endpoint = urlsplit(url)
        if endpoint.scheme not in ("http", "https") or not endpoint.hostname or forbidden_metadata_host(endpoint.hostname):
            raise ValueError
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(endpoint.hostname, port, type=socket.SOCK_STREAM), timeout=5)
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}
        if not addresses or any(forbidden_metadata_host(str(ip)) or ip.is_multicast or ip.is_unspecified for ip in addresses):
            raise ValueError
        chosen = sorted(addresses, key=lambda ip: (ip.version, int(ip)))[0]
        host = f"[{chosen}]" if chosen.version == 6 else str(chosen)
        target = f"{endpoint.scheme}://{host}:{port}{endpoint.path or '/'}"
        if endpoint.query:
            target += f"?{endpoint.query}"
        return target, {"Host": endpoint.netloc}, {"sni_hostname": endpoint.hostname}
    except (ValueError, OSError, asyncio.TimeoutError):
        raise ValueError("HTTP destination unavailable or prohibited") from None


def tls_verify() -> ssl.SSLContext | bool:
    """Trust an explicitly configured private CA without enabling environment proxies."""
    ca_file = get_settings().integration_ca_file
    if ca_file is None:
        return True
    try:
        return ssl.create_default_context(cafile=str(ca_file))
    except (OSError, ValueError):
        raise AppError(
            "出站 HTTPS CA 文件不可用", code="INTEGRATION_TLS_CONFIG_INVALID", status_code=503
        ) from None


class AdapterConfig(ApiModel):
    model_config = ConfigDict(extra="forbid")
    origin: str | None = None
    query_path: str | None = Field(default=None, max_length=255)
    probe_path: str | None = Field(default=None, max_length=255)
    submit_path: str | None = Field(default=None, max_length=255)
    submit_paths: list[str] = Field(default_factory=list, max_length=100)
    actions: list[str] = Field(default_factory=list, max_length=100)
    effect_paths: dict[str, str] = Field(default_factory=dict, max_length=100)
    token: SecretStr | None = None
    # Optional HMAC-SHA256 Webhook signing secret (plaintext or fernet$…); see integration/signing.py.
    signing_secret: SecretStr | None = None

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

    @field_validator("effect_paths")
    @classmethod
    def effect_paths_valid(cls, value):
        for key, path in value.items():
            if not key or len(key) > 64 or len(path) > 255:
                raise ValueError("Effect key/path is invalid")
            cls.relative_path(path)
        return value

    @field_validator("submit_paths")
    @classmethod
    def submit_paths_valid(cls, value):
        for path in value:
            cls.relative_path(path)
        if len(value) != len(set(value)):
            raise ValueError("Duplicate submit paths")
        return value


class LocalDemoConfig(ApiModel):
    model_config = ConfigDict(extra="forbid")

    ontology_id: int = Field(gt=0, strict=True)
    system_code: str = Field(min_length=1, max_length=64)
    origin: str
    submit_path: str
    query_path: str

    @field_validator("origin")
    @classmethod
    def local_origin(cls, value):
        parts = urlsplit(value)
        try:
            port = parts.port
        except ValueError:
            raise ValueError("Invalid local demo port") from None
        if not port or value != f"https://127.0.0.1:{port}":
            raise ValueError("Local demo origin must be an explicit HTTPS loopback port")
        return value

    @field_validator("submit_path", "query_path")
    @classmethod
    def local_path(cls, value):
        if not re.fullmatch(r"/[A-Za-z0-9_~./-]*", value) or any(
            part in ("", ".", "..") for part in value[1:].split("/")
        ):
            raise ValueError("Local demo path must be an exact canonical path")
        return value


def local_demo_selections() -> list[LocalDemoConfig]:
    settings = get_settings()
    if not settings.integration_local_demo:
        return []
    try:
        value = json.loads(settings.integration_local_demo)
        return [LocalDemoConfig.model_validate(item) for item in (value if isinstance(value, list) else [value])][:16]
    except (ValidationError, ValueError, TypeError):
        return []


def local_demo_selection(ontology_id: int | None = None, system_code: str | None = None) -> LocalDemoConfig | None:
    return next((t for t in local_demo_selections() if (ontology_id is None or t.ontology_id == ontology_id) and (system_code is None or t.system_code == system_code)), None)


def local_demo_config(ontology_id: int | None = None, system_code: str | None = None) -> LocalDemoConfig | None:
    return local_demo_selection(ontology_id, system_code) if get_settings().env in ("dev", "test") else None


def local_demo_ready(target: LocalDemoConfig) -> bool:
    settings = get_settings()
    if not settings.integration_real_connect or settings.integration_adapters is None:
        return False
    try:
        configs = json.loads(settings.integration_adapters.get_secret_value())
        config = AdapterConfig.model_validate(configs[f"{target.ontology_id}/{target.system_code}"])
    except (ValueError, TypeError, KeyError, ValidationError):
        return False
    return config.submit_path == target.submit_path and config.query_path == target.query_path


def local_demo_matches(system: ExtSystem) -> bool:
    target = local_demo_config(system.ontology_id, system.code)
    return bool(
        target
        and local_demo_ready(target)
        and getattr(system, "local_only", False)
        and system.ontology_id == target.ontology_id
        and system.code == target.system_code
        and system.env == "测试"
        and system.category == "Webhook"
        and system.status != ExtSystemStatus.disabled
        and system.endpoint == target.origin
    )


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
            endpoint.scheme not in ("http", "https")
            or not endpoint.hostname
            or forbidden_metadata_host(endpoint.hostname)
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise unavailable("外部系统地址须为不含凭据或查询参数的 HTTP(S) 地址")
        self.origin = f"{endpoint.scheme}://{endpoint.netloc}"
        self.verify = tls_verify()

    def path(self, operation: str) -> str:
        if getattr(self.system, "local_only", False):
            target = local_demo_config(self.system.ontology_id, self.system.code)
            if (
                not local_demo_matches(self.system)
                or operation not in ("submit", "query")
                or self.config.submit_path != target.submit_path
                or self.config.query_path != target.query_path
            ):
                raise unavailable("本机演示目标未启用或配置已变化")
        value = {
            "query": self.config.query_path,
            "probe": self.config.probe_path,
            "submit": self.config.submit_path,
        }.get(operation)
        if not value:
            raise unavailable(
                {
                    "query": "该外部系统未配置幂等查询路径",
                    "probe": "该外部系统未配置只读连通性探测路径",
                    "submit": "该外部系统未配置真实动作执行路径",
                }.get(operation, "适配器操作无效")
            )
        return value

    async def request(self, operation: str, key: str | None = None) -> httpx.Response:
        path = self.path(operation)
        url = f"{self.origin}{path}"
        headers = {"Accept": "application/json", "X-Ontology-Id": str(self.system.ontology_id)}
        extensions = {}
        if operation == "probe" and hasattr(self, "probe_policy"):
            endpoint = urlsplit(self.origin)
            hostname = endpoint.hostname
            port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
            try:
                addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM), timeout=5)
                candidates = {ipaddress.ip_address(item[4][0]) for item in addresses}
                if not candidates or any(not probe_address_allowed(ip, port, self.probe_policy) for ip in candidates):
                    raise ValueError
                selected = sorted(candidates, key=lambda ip: (ip.version, int(ip)))[0]
                host = f"[{selected}]" if selected.version == 6 else str(selected)
                url = f"{endpoint.scheme}://{host}:{port}{path}"
                headers["Host"] = endpoint.netloc
                extensions["sni_hostname"] = hostname
            except (ValueError, OSError, asyncio.TimeoutError):
                raise unavailable("探测地址解析失败或包含未授权地址") from None
        else:
            try:
                url, host_header, extensions = await pin_http_target(url)
                headers.update(host_header)
            except ValueError:
                raise unavailable("目标地址解析失败或不可用") from None
        if self.config.token and self.system.auth_type != NO_AUTH:
            headers["Authorization"] = f"Bearer {self.config.token.get_secret_value()}"
        try:
            async with httpx.AsyncClient(
                timeout=max(0.1, min(self.system.timeout_ms / 1000, 60)),
                follow_redirects=False,
                trust_env=False,
                verify=self.verify,
            ) as client:
                response = await client.get(
                    url, params={"idemKey": key} if key is not None else None, headers=headers, extensions=extensions
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


def probe_address_allowed(address, port, policy):
    if (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped) or forbidden_metadata_host(str(address)) or address.is_multicast or address.is_unspecified:
        return False
    return any(address in network and port in ports for network, ports in policy)


def get_probe_adapter(system: ExtSystem) -> HttpAdapter:
    """Use a stored probe only inside the deployment's explicit network ceiling."""
    settings = get_settings()
    if not settings.integration_real_connect or system.status == ExtSystemStatus.disabled:
        raise unavailable("真实探测未启用或系统不可用")
    try:
        endpoint = urlsplit(system.endpoint)
        if not system.probe_path:
            address = ipaddress.ip_address(endpoint.hostname or "")
            if not probe_address_allowed(address, 1, [(ipaddress.ip_network(f"{address}/{address.max_prefixlen}"), [1])]):
                raise ValueError
            return get_adapter(system, "probe")
        if system.local_only:
            raise ValueError
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        if endpoint.port == 0:
            raise ValueError
        policy = json.loads(settings.integration_probe_networks or "[]")
        if not isinstance(policy, list) or len(policy) > 100:
            raise ValueError
        parsed_policy = []
        for item in policy:
            if not isinstance(item, dict) or set(item) != {"cidr", "ports"} or not isinstance(item["ports"], list):
                raise ValueError
            network = ipaddress.ip_network(item["cidr"], strict=True)
            if network.prefixlen < (8 if network.version == 4 else 32):
                raise ValueError
            if any(type(p) is not int or not 1 <= p <= 65535 for p in item["ports"]):
                raise ValueError
            parsed_policy.append((network, item["ports"]))
        if not parsed_policy:
            raise ValueError
        config = AdapterConfig(probe_path=system.probe_path)
        adapter = HttpAdapter(system, config)
        if system.endpoint != adapter.origin:
            raise ValueError
        adapter.probe_policy = parsed_policy
        return adapter
    except (ValueError, TypeError, ValidationError):
        raise unavailable("探测目标或部署网络授权配置无效") from None


def get_adapter(system: ExtSystem, operation: str = "query") -> HttpAdapter:
    settings = get_settings()
    if getattr(system, "local_only", False) and (
        operation not in ("submit", "query") or not local_demo_matches(system)
    ):
        raise unavailable("本机演示目标未启用或配置已变化")
    if system.status == ExtSystemStatus.disabled:
        raise unavailable("外部系统已停用，不能自动查询")
    if not settings.integration_real_connect or settings.integration_adapters is None:
        raise unavailable(
            "真实外部调用未启用或未配置；请配置 RENGU_INTEGRATION_REAL_CONNECT 与 RENGU_INTEGRATION_ADAPTERS"
        )
    try:
        mapping = json.loads(settings.integration_adapters.get_secret_value())
        if not isinstance(mapping, dict) or type(system.ontology_id) is not int:
            raise ValueError
        binding = mapping.get(f"{system.ontology_id}/{system.code}")
        if binding is None and system.ontology_id == 1:
            binding = mapping.get(system.code)
        if not isinstance(binding, dict):
            raise ValueError
        config = AdapterConfig.model_validate(binding)
    except (ValueError, TypeError, ValidationError):
        raise unavailable("该系统的适配器配置缺失或格式无效") from None
    adapter = HttpAdapter(system, config)
    target = local_demo_config(system.ontology_id, system.code)
    allowed_origin = target.origin if target else config.origin
    if not allowed_origin or allowed_origin != adapter.origin or system.endpoint != adapter.origin:
        raise unavailable("部署未授权该系统的精确地址")
    if operation != "effect":
        adapter.path(operation)
    return adapter
