"""Quality definition inputs shared by draft saving and publication validation."""

from typing import Literal

from pydantic import Field, field_validator

from app.core.schema import ApiModel


class CheckDefinition(ApiModel):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[^\x00]+$")
    type_key: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00]+$")
    kind: Literal["新鲜度", "空值率", "唯一性", "引用完整性"]
    threshold: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00]+$")
    schedule: Literal["手动", "每 2 小时", "每天 23:30", "每周一 06:00"] = "手动"

    @field_validator("name", "type_key", "threshold", mode="before")
    @classmethod
    def valid_text(cls, value):
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("内容不能为空")
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("文本包含无效Unicode字符") from None
            return value.strip()
        return value


class ChangeOptions(ApiModel):
    cr_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[^\x00]+$")
    lock_version: int | None = Field(default=None, ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=40)
    reason: str = ""

    @field_validator("cr_id", "title", "reason")
    @classmethod
    def safe_text(cls, value):
        if value is not None:
            if "\x00" in value:
                raise ValueError("文本不能包含空字符")
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("文本包含无效Unicode字符") from None
        return value


class CheckSave(CheckDefinition, ChangeOptions):
    pass
