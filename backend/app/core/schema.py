"""接口 Schema 基类与共用类型。

约定：
- 对外 JSON 字段一律 camelCase（和前端 mock 的 TS interface 一模一样），Python 里写 snake_case；
- 枚举对外输出中文取值（和 mock 一致），数据库里存英文 code，见 app/core/enums.py；
- 时间**按字段**沿用 mock 原样格式（PDE 定）：默认到分钟 `DateTimeMin`（"YYYY-MM-DD HH:mm"），
  mock 带秒的字段用 `DateTimeSec`（"YYYY-MM-DD HH:mm:ss"），只有日期的用 `DisplayDate`；
  业务时区 Asia/Shanghai。
  库里一律存 timestamptz。入参三种都接受：分钟格式、秒格式、ISO 8601（带时区）。
"""

from datetime import date, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer
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


DATETIME_FORMAT = "%Y-%m-%d %H:%M"
DATETIME_SEC_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_dt(v: Any) -> Any:
    if isinstance(v, str) and len(v) in (16, 19) and v[10] == " ":  # "2026-10-24 21:17" / "…:06"
        return parse_display_dt(v)
    return v


def _fmt_dt(v: datetime) -> str:
    if v.tzinfo is None:
        v = v.replace(tzinfo=_tz())
    return v.astimezone(_tz()).strftime("%Y-%m-%d %H:%M")


def _fmt_dt_seconds(v: datetime) -> str:
    if v.tzinfo is None:
        v = v.replace(tzinfo=_tz())
    return v.astimezone(_tz()).strftime("%Y-%m-%d %H:%M:%S")


# 默认：显示到分钟（mock 里绝大多数时间字段）
DateTimeMin = Annotated[
    datetime, BeforeValidator(_parse_dt), PlainSerializer(_fmt_dt, return_type=str, when_used="json")
]
# mock 里带秒的字段（例如执行记录 runsOf 的 at "2026-10-25 13:32:06"、审计、事件流）
DateTimeSec = Annotated[
    datetime, BeforeValidator(_parse_dt), PlainSerializer(_fmt_dt_seconds, return_type=str, when_used="json")
]
DisplayDate = Annotated[date, PlainSerializer(lambda d: d.isoformat(), return_type=str, when_used="json")]

# 旧名（a30beb6），保留兼容
DisplayDateTime = DateTimeMin
DisplayDateTimeSec = DateTimeSec

# 接口里前端看到的 id = 业务编号（库里是 code 列，代理主键 id 不出接口）。
# 输出模型写 `id: CodeId`，从 ORM 读 code 属性；入参 JSON 里仍叫 "id"。
CodeId = Annotated[str, Field(validation_alias=AliasChoices("code", "id"))]


def parse_display_dt(s: str) -> datetime:
    """seed 和导入用：把 mock 里的 "YYYY-MM-DD HH:mm" 或 "YYYY-MM-DD HH:mm:ss" 转成带时区的 datetime。"""
    fmt = DATETIME_SEC_FORMAT if len(s) == 19 else DATETIME_FORMAT
    return datetime.strptime(s, fmt).replace(tzinfo=_tz())
