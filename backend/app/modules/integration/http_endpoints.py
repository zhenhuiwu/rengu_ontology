"""Named same-origin endpoints; no credentials or executable expressions."""
import re
from typing import Literal
from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator
from app.core.errors import AppError
from app.core.schema import ApiModel
from app.modules.integration.http_mapping import KEY, validate_mapping


class HttpEndpoint(ApiModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    purpose: Literal["probe", "action"]
    method: Literal["GET", "POST", "PUT", "PATCH"]
    path: str = Field(min_length=1, max_length=255)
    http_mapping: dict | None = None

    @field_validator("name", "description")
    @classmethod
    def clean_name(cls, value):
        if value is None:
            return None
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("接口名称无效")
        return value.strip()

    @field_validator("path")
    @classmethod
    def clean_path(cls, path):
        if not re.fullmatch(r"/[A-Za-z0-9_~./-]*", path) or (path != "/" and any(p in ("", ".", "..") for p in path[1:].split("/"))):
            raise ValueError("路径须为同源规范绝对路径")
        return path

    @model_validator(mode="after")
    def check_mapping(self):
        if not self.name:
            raise ValueError("接口名称无效")
        if self.purpose == "probe":
            if self.method != "GET" or self.http_mapping is not None:
                raise ValueError("探测接口只能使用 GET 且不支持请求映射")
        else:
            if self.method not in ("POST", "PUT", "PATCH"):
                raise ValueError("动作接口仅支持 POST、PUT 或 PATCH")
            rows = self.http_mapping.get("request", []) if isinstance(self.http_mapping, dict) else []
            rows = rows if isinstance(rows, list) else []
            params = {row["source"][7:] for row in rows if isinstance(row, dict) and isinstance(row.get("source"), str) and row["source"].startswith("params.") and KEY.fullmatch(row["source"][7:])}
            validate_mapping(self.http_mapping, params)
        return self


class HttpEndpointsIn(ApiModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    version: int = Field(ge=1)
    endpoints: list[HttpEndpoint] = Field(max_length=32)

    @model_validator(mode="after")
    def unique(self):
        if len({v.id for v in self.endpoints}) != len(self.endpoints) or sum(v.purpose == "probe" for v in self.endpoints) > 1:
            raise ValueError("接口编号不能重复，探测接口最多一个；动作可通过不同编号复用同一路径")
        return self


def endpoint_conflict(reason, message, fields, *, status_code=409):
    return AppError(
        f"{message}（{'、'.join(fields[:4])}）", code=f"ACTION_HTTP_ENDPOINT_{reason}", status_code=status_code,
        details={"reason": reason.lower(), "fields": [{"field": field, "message": message} for field in fields]},
    )


def validate_endpoint_params(mapping, params):
    rows = mapping.get("request", []) if isinstance(mapping, dict) else []
    if not isinstance(rows, list):
        return  # The existing mapping validator reports malformed request containers as 422.
    fields = [f"httpMapping.request.{index}.source" for index, row in enumerate(rows) if isinstance(row, dict)
              and isinstance(row.get("source"), str) and row["source"].startswith("params.") and row["source"][7:] not in params]
    if fields:
        raise endpoint_conflict("PARAM_UNDEFINED", "接口映射引用了动作未定义的参数，请先补齐动作参数或调整映射，再重新选择接口并发布", fields, status_code=422)


def mapping_fields(left, right, field="httpMapping"):
    """Return configuration locations only; never echo mapped values."""
    if left == right:
        return []
    if isinstance(left, dict) and isinstance(right, dict):
        return [path for key in sorted(left.keys() | right.keys()) for path in mapping_fields(left.get(key), right.get(key), f"{field}.{key}")]
    if isinstance(left, list) and isinstance(right, list):
        return [path for index in range(max(len(left), len(right))) for path in mapping_fields(left[index] if index < len(left) else None, right[index] if index < len(right) else None, f"{field}.{index}")]
    return [field]


def require_selected_endpoint(system, endpoint_id, endpoint, mapping, params=None):
    matches = [v for v in getattr(system, "http_endpoints", None) or [] if isinstance(v, dict) and v.get("id") == endpoint_id]
    if len(matches) != 1:
        raise endpoint_conflict("NOT_FOUND", "所选接口目录项不存在或不唯一，请在外部系统接口目录确认后重新选择并发布", ["httpEndpointId"])
    try:
        item = HttpEndpoint.model_validate(matches[0])
    except (ValidationError, AppError):
        raise endpoint_conflict("CATALOG_INVALID", "所选接口目录项配置无效，请修正外部系统接口目录后重新选择并发布", ["httpEndpointId"]) from None
    try:
        method, path = endpoint.split(" ") if isinstance(endpoint, str) else (None, None)
        if method not in ("POST", "PUT", "PATCH"):
            raise ValueError
        HttpEndpoint.clean_path(path)
    except (TypeError, ValueError):
        raise endpoint_conflict("FORMAT_INVALID", "动作接口须为“POST、PUT 或 PATCH + 空格 + 同源规范路径”，请重新选择接口并发布", ["endpoint"]) from None
    if item.purpose != "action" or endpoint != f"{item.method} {item.path}":
        raise endpoint_conflict("CHANGED", "所选接口的用途、方法或路径已变化，请重新选择接口并发布", ["httpEndpointId", "endpoint"])
    if params is not None:
        validate_endpoint_params(item.http_mapping, params)
        validate_endpoint_params(mapping, params)
    if mapping != item.http_mapping:
        raise endpoint_conflict("MAPPING_CHANGED", "动作映射与接口目录不一致，请核对标记字段后重新选择接口并发布", mapping_fields(mapping, item.http_mapping)[:64] or ["httpMapping"])
    return item


def selected_endpoint(system, endpoint_id, endpoint, mapping):
    # Keep the historical boolean lookup contract for callers outside validation paths.
    try:
        return require_selected_endpoint(system, endpoint_id, endpoint, mapping)
    except AppError:
        return None
