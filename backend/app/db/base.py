"""ORM 基类、命名规范和公共列 mixin。

每张表按需组合：
- IdMixin / StrIdMixin：主键（见 CONVENTIONS.md「主键」）
- AuditColumnsMixin：created_at/by、updated_at/by（所有业务表都要）
- SoftDeleteMixin：deleted_at/by（可删除的运行时数据；本体定义不用，删除走变更单）
- LockVersionMixin：乐观锁 lock_version（前端会并发编辑的表：变更单、设置、角色……）
- SeedMarkMixin：is_seed（seed 写入的行；mock 数据违反的唯一约束改成 `WHERE NOT is_seed` 的部分唯一索引）
- OntologyVersionMixin：ontology_ver（运行时记录要记下“按哪个本体版本产生”：问数轮次、告警、动作、事件）
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Index, Integer, MetaData, String, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.core import clock

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    """没有业务编号的表：bigint 自增（IDENTITY）。"""

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)


class StrIdMixin:
    """有前端可见业务编号的表（CR-0011、ACT-20261025-0001、K-018）：编号本身做主键。"""

    id: Mapped[str] = mapped_column(String(64), primary_key=True)


class AuditColumnsMixin:
    # 时间由应用时钟写入（app/core/clock.py）；server_default 只是直接写 SQL 时的兜底
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=clock.now, server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=clock.now,
        onupdate=clock.now,
        server_default=func.now(),
        nullable=False,
    )
    updated_by: Mapped[str | None] = mapped_column(String(64))


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(64))


class LockVersionMixin:
    """乐观锁：每次 UPDATE 自动 +1；版本不对抛 StaleDataError（BaseRepository 转成 VERSION_CONFLICT）。"""

    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:  # noqa: N805
        return {"version_id_col": cls.lock_version}


class OntologyVersionMixin:
    ontology_ver: Mapped[str | None] = mapped_column(String(32), index=True)


class SeedMarkMixin:
    """seed 写入的行 is_seed=true（loader 自动设置），业务新写入的行为 false。"""

    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


def unique_except_seed(table: str, *columns: str) -> Index:
    """部分唯一索引：只约束非 seed 行。前端 mock 本身有重复（例如重复的幂等键）时用。

    __table_args__ = (unique_except_seed("dy_action_run", "idempotency_key"),)
    """
    return Index(
        f"uq_{table}_{'_'.join(columns)}_nonseed",
        *columns,
        unique=True,
        postgresql_where=text("NOT is_seed"),
    )
