"""ORM 基类、命名规范和公共列 mixin。

表的公共列（PDE 定，一期）：
- 可修改的表 → `MutableTableMixin`：created_at、updated_at、created_by、is_seed、lock_version
- 只追加的表（日志、版本、事件、命中记录）→ `AppendOnlyTableMixin`：created_at、created_by、is_seed；
  没有 lock_version，也不软删除
- 软删除**不是默认**：只有前端有「删除 / 恢复」的表才额外混入 `SoftDeleteMixin`（deleted_at/by）
- 主键：所有表都是代理键 id bigint（IdMixin）；业务编号放 code（CodeMixin，唯一）
  或自然列（ver、api_name）+ 唯一约束；外键指向 id
- OntologyVersionMixin：release_id → ontology_release.id（运行时记录要记下“按哪个本体版本产生”：
  问数轮次、告警、动作、事件；接口仍返回 ver）

表名：业务名词、snake_case 单数、**不加模块前缀**；只有和 SQL 关键字冲突或有歧义时才加限定词
（ontology_release、sys_user、sys_role、biz_order）。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
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

    def __init_subclass__(cls, **kwargs):
        # Platform identities/configuration are shared; every domain row, including children, is scoped.
        scoped = (
            cls.__module__.startswith("app.modules.")
            and not cls.__module__.startswith(("app.modules.admin.", "app.modules.ontology."))
        ) or cls.__module__ == "app.common.outbox"
        if scoped and "__tablename__" in cls.__dict__:
            cls.ontology_id = mapped_column(
                BigInteger,
                ForeignKey("ontology_workspace.id"),
                nullable=False,
                index=True,
                server_default=text("NULLIF(current_setting('rengu.ontology_id', true), '')::bigint"),
            )
        super().__init_subclass__(**kwargs)
        if scoped and "__table__" in cls.__dict__:
            table = cls.__table__
            for constraint in list(table.constraints):
                if isinstance(constraint, UniqueConstraint) and "ontology_id" not in constraint.columns:
                    columns = [c.name for c in constraint.columns]
                    table.constraints.remove(constraint)
                    table.append_constraint(
                        UniqueConstraint("ontology_id", *columns, **constraint.dialect_kwargs)
                    )
            for index in list(table.indexes):
                if index.unique and "ontology_id" not in index.columns:
                    table.indexes.remove(index)
                    Index(
                        index.name,
                        table.c.ontology_id,
                        *index.expressions,
                        unique=True,
                        **index.dialect_kwargs,
                    )


class IdMixin:
    """**所有表**的主键：代理键 id bigint 自增（IDENTITY）。外键一律指向 id。"""

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)


class CodeMixin(IdMixin):
    """有前端可见业务编号的表（CR-0011、ACT-20261025-3834、K-018）：id 仍是代理主键，编号放 code（唯一）。

    接口里前端看到的 `id` 字段 = code（Pydantic 层映射）；路由参数也是 code，用 BaseRepository.get_by_code。
    业务键本来就有自然列名的（ontology_release.ver、对象类型 api_name）不用这个 mixin，
    自己声明列 + UniqueConstraint。
    """

    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="业务编号（前端 id）")


class CreatedColumnsMixin:
    # 时间由应用时钟写入（app/core/clock.py）；server_default 只是直接写 SQL 时的兜底
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=clock.now, server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(64))


class UpdatedAtMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=clock.now,
        onupdate=clock.now,
        server_default=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """只给前端有「删除 / 恢复」的表用（不是默认列）。"""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[str | None] = mapped_column(String(64))


class LockVersionMixin:
    """乐观锁：每次 UPDATE 自动 +1；版本不对抛 StaleDataError（BaseRepository 转成 VERSION_CONFLICT）。"""

    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:  # noqa: N805
        return {"version_id_col": cls.lock_version}


class OntologyVersionMixin:
    """运行时记录「按哪个本体版本产生」：外键 release_id → ontology_release.id（不存版本号字符串）。

    接口仍返回版本号 ver：模型里加 `release = relationship(OntologyRelease, lazy="joined")`，
    schema 用 `ver: str | None = Field(validation_alias=AliasPath("release", "ver"))` 之类取。
    """

    release_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ontology_release.id"), index=True, comment="本体版本（ontology_release.id）"
    )


class SeedMarkMixin:
    """seed 写入的行 is_seed=true（loader 自动设置），业务新写入的行为 false。"""

    is_seed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class MutableTableMixin(CreatedColumnsMixin, UpdatedAtMixin, SeedMarkMixin, LockVersionMixin):
    """可修改的表的标准公共列：created_at、updated_at、created_by、is_seed、lock_version。"""


class AppendOnlyTableMixin(CreatedColumnsMixin, SeedMarkMixin):
    """只追加的表（日志、版本、事件、命中）：created_at、created_by、is_seed；无 lock_version、无软删除。"""


def unique_except_seed(table: str, *columns: str) -> Index:
    """部分唯一索引：只约束非 seed 行。前端 mock 本身有重复（例如重复的幂等键）时用。

    __table_args__ = (unique_except_seed("action_run", "idem_key"),)
    """
    return Index(
        f"uq_{table}_{'_'.join(columns)}_nonseed",
        *columns,
        unique=True,
        postgresql_where=text("NOT is_seed"),
    )
