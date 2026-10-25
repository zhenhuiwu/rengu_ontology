"""跨方言列类型。模型里只用这里的类型，不要直接 import postgresql 方言类型。"""

from enum import Enum as PyEnum

from sqlalchemy import JSON, Enum, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

# JSON：PG 上是 JSONB，其他库退化为 JSON / CLOB（以后达梦/金仓在 dialects 里补 variant）
JsonType = JSON().with_variant(JSONB(), "postgresql")


def StrArray(length: int = 64):  # noqa: N802
    """字符串数组：PG 用 text[]，其他库用 JSON。"""
    return JSON().with_variant(ARRAY(String(length)), "postgresql")


def enum_column(enum_cls: type[PyEnum], length: int = 32) -> Enum:
    """枚举列：存成员名（英文 code），varchar + CHECK 约束，不建 PG 原生 enum。"""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        create_constraint=True,
        name=f"{enum_cls.__name__.lower()}",
        validate_strings=True,
    )
