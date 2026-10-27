"""Explicit published metric defaults; no free-text time inference."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MetricDefaultRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["rolling_days"]
    days: int = Field(strict=True, ge=1, le=93)


def default_range(value):
    return MetricDefaultRange.model_validate(value).model_dump()


def range_label(value):
    if value is None:
        return "未设置"
    try:
        return f"最近{default_range(value)['days']}天（含今天）"
    except ValueError:
        return "默认时间范围不可用"
