"""Pydantic 输入输出（继承 app.core.schema.ApiModel，字段对齐前端 mock 的 TS interface）。"""

from typing import Any

from pydantic import Field

from app.core.schema import ApiModel
from app.modules.action.models import TypeStatus
from app.modules.integration.models import ExtSystemStatus


class ExtSystemOut(ApiModel):
    """EXT_SYSTEMS 一行。密钥不返回（表里也没有明文）。"""

    id: str = Field(validation_alias="code")
    name: str
    vendor: str
    category: str
    endpoint: str
    auth: str = Field(validation_alias="auth_type")
    auth_detail: str
    idempotent: bool
    idem_key: str = Field(validation_alias="idem_header")
    timeout_ms: int
    max_retry: int
    retry: str = Field(validation_alias="retry_text")
    rate_limit: str
    owner: str
    status: ExtSystemStatus
    env: str
    usage: dict[str, Any] | None = None


class LinkedActionTypeOut(ApiModel):
    id: str = Field(validation_alias="code")
    name: str
    group: str = Field(validation_alias="grp")
    status: TypeStatus
    endpoint: str


class ExtSystemDetailOut(ExtSystemOut):
    action_types: list[LinkedActionTypeOut] = []


class ExtSystemListOut(ApiModel):
    items: list[ExtSystemOut]
