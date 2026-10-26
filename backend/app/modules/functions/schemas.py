import keyword
import re
from typing import Any

from pydantic import Field, field_validator

from app.core.schema import ApiModel
from app.modules.functions.runtime import json_value


class ParamIn(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=128)
    obj_type: str | None = Field(default=None, max_length=128)
    desc: str = Field(default="", max_length=8_192)

    @field_validator("name", "type", "obj_type", "desc")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        if value is not None:
            json_value(value)
        return value


class SaveIn(ApiModel):
    title: str = Field(min_length=1, max_length=128)
    desc: str = Field(default="", max_length=8_192)
    timeout_s: int = Field(default=5, ge=1, le=60)
    code: str = Field(min_length=1, max_length=65_536)
    lock_version: int | None = Field(default=None, ge=1)
    cr_id: str | None = Field(default=None, min_length=1, max_length=64)
    cr_lock_version: int | None = Field(default=None, ge=1)
    change_title: str | None = Field(default=None, min_length=1, max_length=40)
    reason: str = Field(default="", max_length=8_192)

    @field_validator("title", "desc", "code", "cr_id", "change_title", "reason")
    @classmethod
    def text(cls, value: str | None) -> str | None:
        if value is not None:
            json_value(value)
        return value

    @field_validator("title")
    @classmethod
    def title_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写中文名")
        return value.strip()


class CreateIn(SaveIn):
    name: str = Field(min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) or keyword.iskeyword(value):
            raise ValueError("函数名须为小写英文字母开头的字母、数字或下划线")
        return value


class RollbackIn(ApiModel):
    to_version: str = Field(min_length=1, max_length=32)
    lock_version: int = Field(ge=1)
    cr_id: str | None = Field(default=None, min_length=1, max_length=64)
    cr_lock_version: int | None = Field(default=None, ge=1)
    change_title: str | None = Field(default=None, min_length=1, max_length=40)
    reason: str = Field(default="", max_length=8_192)

    @field_validator("to_version", "cr_id", "change_title", "reason")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        return SaveIn.text(value)


class TestIn(ApiModel):
    input: dict[str, Any]

    @field_validator("input")
    @classmethod
    def valid_input(cls, value: dict[str, Any]) -> dict[str, Any]:
        return json_value(value)
