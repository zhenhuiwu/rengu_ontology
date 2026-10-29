"""Pydantic 输入输出（继承 app.core.schema.ApiModel，字段对齐前端 mock 的 TS interface）。"""

from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

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
    # 列表接口填：写回到该系统的动作类型数（前端原来用 ACTION_TYPES.filter(sysId) 数）；详情接口看 actionTypes
    action_count: int | None = None


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
    total: int = 0
    page: int = 1
    size: int = 10


class ExtSystemIn(ApiModel):
    """新建 / 编辑外部系统的请求体：字段同 ExtSystemOut（前端表单 blank() 的字段）。

    `id` 不收（编号由后台生成）。

    校验规则包括名称、供应商必填，HTTP(S) 地址，支持幂等时幂等键必填；
    超时 1～60 秒）；`retry` 不传时按前端 retryText() 生成。不支持幂等时幂等键存 '-'、最多重试存 0（同前端）。
    """

    name: str = Field(max_length=64)
    vendor: str = Field(max_length=64)
    category: str = Field(default="", max_length=32)
    endpoint: str = Field(max_length=255)
    auth: str = Field(default="", max_length=64)
    auth_detail: str = ""
    idempotent: bool = False
    idem_key: str = Field(default="", max_length=64)
    timeout_ms: int = Field(ge=1000, le=60000)
    max_retry: int = Field(default=0, ge=0, le=5)
    retry: str | None = None
    rate_limit: str = Field(default="", max_length=64)
    owner: str = Field(default="", max_length=64)
    status: ExtSystemStatus = ExtSystemStatus.ok
    env: str = Field(default="生产", max_length=16)
    usage: dict[str, Any] | None = None

    @field_validator("name", "vendor", "endpoint", "auth", "idem_key")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not v:
            raise PydanticCustomError("value_error", "请填写系统名称")
        return v

    @field_validator("vendor")
    @classmethod
    def _vendor(cls, v: str) -> str:
        if not v:
            raise PydanticCustomError("value_error", "请填写供应商")
        return v

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v: str) -> str:
        try:
            parts = urlsplit(v)
            port = parts.port
            valid = (parts.scheme in ("http", "https") and parts.hostname and not parts.username
                     and not parts.password and not parts.query and not parts.fragment
                     and not parts.path and not any(c.isspace() or ord(c) < 32 for c in v)
                     and (port is None or 1 <= port <= 65535))
        except ValueError:
            valid = False
        if not valid:
            raise PydanticCustomError("value_error", "接口地址需为不含路径、凭据或查询参数的 HTTP(S) 来源地址")
        return v

    @model_validator(mode="after")
    def _idem(self) -> Self:
        if self.idempotent and not self.idem_key:
            raise PydanticCustomError("value_error", "支持幂等时需填写幂等键")
        return self


class ExtSystemTestIn(ApiModel):
    op: str | None = None


class ExtSystemTestOut(ApiModel):
    """测试调用结果。字段同前端 ExtCall（页面把它插到「最近调用」最前面）。

    文档 §3.1 的 {http, ms, result, msg} 是它的子集。
    """

    id: str
    sys_id: str
    time: str
    action: str
    target: str
    method: str
    path: str
    idem_key: str | None = None
    ms: int
    http: int | None
    result: str
    attempt: int
    actor: str
    msg: str


class ExtCallOut(ApiModel):
    """外部调用记录（前端 extsys.ts ExtCall）。runId 只有写回调用结果未知时有（mock 同），其余为 null。"""

    id: str
    sys_id: str
    time: str
    action: str
    target: str
    method: str
    path: str
    idem_key: str | None = None
    ms: int
    http: int | None
    result: str
    attempt: int
    actor: str
    msg: str
    run_id: str | None = None


class ExtCallPageOut(ApiModel):
    items: list[ExtCallOut]
    total: int
    page: int
    size: int
    # 「最近调用」单选按钮上的数字，键 = 按钮文字（全部 / 成功 / 失败 / 结果未知），不受 result 筛选影响
    counts: dict[str, int]
