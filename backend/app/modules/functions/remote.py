"""Deployment-authorized pure computation targets. Never accepts a caller URL or credential."""

import asyncio
import hashlib
import ipaddress
import json
import socket
from urllib.parse import urlsplit

import httpx
from pydantic import ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import AppError
from app.core.request_id import get_request_id
from app.core.schema import ApiModel
from app.modules.functions.remote_schema import validate_schema, validate_value
from app.modules.functions.runtime import MAX_BYTES, json_value
from app.modules.integration.adapters import AdapterConfig, forbidden_metadata_host, tls_verify

SLOTS = asyncio.Semaphore(8)


def unavailable(message="远程计算目标未获部署授权或配置不可用"):
    return AppError(message, code="FUNCTION_REMOTE_UNAVAILABLE", status_code=503)


class Target(ApiModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    ontology_id: int = Field(gt=0, strict=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=128)
    revision: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    url: str
    networks: list[str] = Field(min_length=1, max_length=32)
    token: SecretStr | None = None
    timeout_s: int = Field(default=10, ge=1, le=60)
    input_schema: dict
    output_schema: dict
    pure: bool = False
    allow_http_loopback: bool = False

    @model_validator(mode="after")
    def controlled(self):
        endpoint = urlsplit(self.url)
        if (not self.pure or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.query or endpoint.fragment or forbidden_metadata_host(endpoint.hostname)):
            raise ValueError("Invalid pure computation target")
        if endpoint.scheme != "https" and not (
            endpoint.scheme == "http" and self.allow_http_loopback
            and ipaddress.ip_address(endpoint.hostname).is_loopback
        ):
            raise ValueError("HTTPS required")
        AdapterConfig.relative_path(endpoint.path or "/")
        if endpoint.port is not None and not 1 <= endpoint.port <= 65535:
            raise ValueError("Invalid port")
        for network in self.networks:
            ipaddress.ip_network(network)
        if self.token and any(ord(c) < 33 or ord(c) > 126 for c in self.token.get_secret_value()):
            raise ValueError("Invalid token")
        validate_schema(self.input_schema, inputs=True)
        validate_schema(self.output_schema)
        return self

    def public(self):
        return {"key": self.key, "label": self.label, "revision": self.revision,
                "timeoutS": self.timeout_s,
                "inputSchema": self.input_schema, "outputSchema": self.output_schema}

    def binding_digest(self):
        value = {"url": self.url, "networks": sorted(self.networks), "timeout": self.timeout_s,
                 "input": self.input_schema, "output": self.output_schema}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class RemoteSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RENGU_", env_file=".env", extra="ignore")
    function_targets: SecretStr = SecretStr("[]")
    function_rule_max_calls: int = Field(default=32, ge=1, le=32)
    function_rule_timeout_s: int = Field(default=20, ge=1, le=30)


def targets(ontology_id):
    try:
        raw = json.loads(RemoteSettings().function_targets.get_secret_value())
        if not isinstance(raw, list) or len(raw) > 64:
            raise ValueError
        result = [Target.model_validate(item) for item in raw]
        keys = [(item.ontology_id, item.key, item.revision) for item in result]
        if len(set(keys)) != len(keys):
            raise ValueError
        return [item for item in result if item.ontology_id == ontology_id]
    except (ValidationError, ValueError, TypeError, RecursionError, AppError):
        raise unavailable("远程计算部署配置无效，请联系管理员") from None


def resolve(ontology_id, remote):
    found = [item for item in targets(ontology_id)
             if item.key == remote.get("targetKey") and item.revision == remote.get("revision")]
    if len(found) != 1:
        raise unavailable()
    target = found[0]
    if "bindingDigest" in remote and remote["bindingDigest"] != target.binding_digest():
        raise unavailable("远程目标部署绑定已变化，请保留旧修订或登记新修订")
    if any(key in remote and remote[key] != value for key, value in (
        ("inputSchema", target.input_schema), ("outputSchema", target.output_schema)
    )):
        raise unavailable("远程目标 schema 与固定版本不一致，请登记新的实现修订")
    return target


async def pinned(target):
    endpoint = urlsplit(target.url)
    port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
    records = await asyncio.get_running_loop().getaddrinfo(endpoint.hostname, port, type=socket.SOCK_STREAM)
    addresses = {ipaddress.ip_address(row[4][0]) for row in records}
    networks = [ipaddress.ip_network(network) for network in target.networks]
    if not addresses or any(forbidden_metadata_host(str(ip)) or ip.is_multicast or ip.is_unspecified
                            or not any(ip in network for network in networks) for ip in addresses):
        raise unavailable("远程目标地址解析不在部署网络授权内")
    address = sorted(addresses, key=lambda ip: (ip.version, int(ip)))[0]
    host = f"[{address}]" if address.version == 6 else str(address)
    url = f"{endpoint.scheme}://{host}:{port}{endpoint.path or '/'}"
    return url, endpoint.netloc, endpoint.hostname


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


async def invoke(ontology_id, code, version, remote, inputs, timeout_s):
    target = resolve(ontology_id, remote)
    validate_value(inputs, target.input_schema, "input")
    limit = min(timeout_s, target.timeout_s)
    try:
        async with asyncio.timeout(limit), SLOTS:
            url, host, server_name = await pinned(target)
            headers = {"Accept": "application/json", "Host": host,
                       "X-Ontology-Id": str(ontology_id), "X-Request-ID": get_request_id() or "-"}
            if target.token:
                headers["Authorization"] = f"Bearer {target.token.get_secret_value()}"
            payload = {"functionId": code, "version": version, "revision": target.revision, "input": inputs}
            async with httpx.AsyncClient(timeout=limit, follow_redirects=False, trust_env=False,  # noqa: SIM117
                                         verify=tls_verify()) as client:
                async with client.stream("POST", url, json=payload, headers=headers,
                                         extensions={"sni_hostname": server_name}) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise AppError("远程计算未返回成功状态",
                                       code="FUNCTION_REMOTE_HTTP_ERROR", status_code=502,
                                       details={"status": response.status_code})
                    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if content_type != "application/json":
                        raise ValueError
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ValueError
            result = json.loads(data, object_pairs_hook=unique_object)
            try:
                json_value(result)
                validate_value(result.get("output") if isinstance(result, dict) else None,
                               target.output_schema, "output")
            except AppError:
                raise ValueError from None
            if (not isinstance(result, dict) or set(result) != {"revision", "output"}
                or result["revision"] != target.revision):
                raise ValueError
            return result["output"]
    except TimeoutError:
        raise AppError("远程计算超时；未自动重试", code="FUNCTION_REMOTE_TIMEOUT", status_code=504) from None
    except httpx.TimeoutException:
        raise AppError("远程计算超时；未自动重试", code="FUNCTION_REMOTE_TIMEOUT", status_code=504) from None
    except (httpx.HTTPError, OSError):
        raise unavailable("远程计算连接失败；未自动重试") from None
    except (ValueError, UnicodeError, RecursionError):
        raise AppError("远程计算响应不符合固定版本合同", code="FUNCTION_REMOTE_INVALID_RESPONSE",
                       status_code=502) from None
