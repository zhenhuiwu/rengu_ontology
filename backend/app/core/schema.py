"""接口 Schema 基类与共用类型。

约定：
- 对外 JSON 字段一律 camelCase（和前端 mock 的 TS interface 一模一样），Python 里写 snake_case；
- 枚举对外输出中文取值（和 mock 一致），数据库里存英文 code，见 app/core/enums.py；
- 时间对外输出 "YYYY-MM-DD HH:mm"（业务时区），和 mock 的显示格式一致，入参也接受 ISO 8601。
"""

from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

from app.core.config import get_settings


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        use_enum_values=False,
    )


def _tz() -> ZoneInfo:
    return ZoneInfo(get_settings().display_tz)


def _parse_dt(v: Any) -> Any:
    if isinstance(v, str) and len(v) == 16 and v[10] == " ":  # "2026-10-24 21:17"
        return datetime.strptime(v, "%Y-%m-%d %H:%M").replace(tzinfo=_tz())
    return v


def _fmt_dt(v: datetime) -> str:
    if v.tzinfo is None:
        v = v.replace(tzinfo=_tz())
    return v.astimezone(_tz()).strftime("%Y-%m-%d %H:%M")


def _fmt_dt_seconds(v: datetime) -> str:
    if v.tzinfo is None:
        v = v.replace(tzinfo=_tz())
    return v.astimezone(_tz()).strftime("%Y-%m-%d %H:%M:%S")


# 页面上显示到分钟的时间（mock 格式）
DisplayDateTime = Annotated[
    datetime, BeforeValidator(_parse_dt), PlainSerializer(_fmt_dt, return_type=str, when_used="json")
]
# 需要秒的时间（审计、事件流）
DisplayDateTimeSec = Annotated[
    datetime, BeforeValidator(_parse_dt), PlainSerializer(_fmt_dt_seconds, return_type=str, when_used="json")
]
DisplayDate = Annotated[date, PlainSerializer(lambda d: d.isoformat(), return_type=str, when_used="json")]

DATETIME_FORMAT = "%Y-%m-%d %H:%M"


def parse_display_dt(s: str) -> datetime:
    """seed 和导入用：把 mock 里的 "YYYY-MM-DD HH:mm" 转成带时区的 datetime。"""
    return datetime.strptime(s, DATETIME_FORMAT).replace(tzinfo=_tz())
