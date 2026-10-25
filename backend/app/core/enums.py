"""枚举约定。

成员名 = 英文 code（存数据库），成员值 = 前端 mock 里的中文取值（接口输出）。
SQLAlchemy 的 Enum 类型默认存成员名，Pydantic 默认输出成员值，正好满足
“库里是稳定的英文 code、页面看到的和 mock 一字不差”。

    class CrStatus(LabelEnum):
        draft = "草稿"          # 前端用中文 → 接口返回中文
        in_review = "审核中"

    class JobState(LabelEnum):
        processing = "doing"     # 前端用英文但叫法不同 → 库里 processing，接口 doing
        finished = "done"

列定义统一用 app.db.types.enum_column(CrStatus)，会生成 varchar + CHECK 约束（不用 PG 原生 enum，
方便以后加取值，也方便迁到达梦/金仓）。
"""

from enum import StrEnum


class LabelEnum(StrEnum):
    """str 子类，方便和字符串比较；value 是中文标签。"""

    @property
    def code(self) -> str:
        return self.name

    @classmethod
    def from_code(cls, code: str) -> "LabelEnum":
        return cls[code]

    def __str__(self) -> str:
        return self.value


class TimelineStatus(StrEnum):
    """时间线条目状态（前端 ActionLog.status 等），本身就是英文，原样输出。"""

    done = "done"
    warn = "warn"
    error = "error"
    unknown = "unknown"


def enum_map(enum_cls: type[LabelEnum]) -> dict[str, str]:
    """{DB code: 接口值}，给文档、seed 校验或前端对照表用。"""
    return {m.name: m.value for m in enum_cls}


def from_api(enum_cls: type[LabelEnum], value: str) -> LabelEnum:
    """接口值（中文 / 前端叫法）→ 枚举；seed transform 里把 mock 的取值转成枚举用。"""
    return enum_cls(value)
