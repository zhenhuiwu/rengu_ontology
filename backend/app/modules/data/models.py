"""ORM 模型（表名 datasource / datasource_run / datasource_table，不加模块前缀；主键 id，外键指向 id）。

前端：src/pages/data/Connections.vue + src/mock/datasources.ts（api-index §3.1）。
新增模型要在 app/db/models.py 里 import。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import LabelEnum
from app.db.base import AppendOnlyTableMixin, Base, CodeMixin, IdMixin, MutableTableMixin
from app.db.types import JsonType, StrArray, enum_column


class DsType(LabelEnum):
    """数据源类型（mock DS_TYPES）。"""

    postgresql = "PostgreSQL"
    dm8 = "达梦 DM8"
    kingbase = "人大金仓 KingbaseES"
    mysql = "MySQL"
    erp = "ERP"


class DsStatus(LabelEnum):
    ok = "正常"
    error = "异常"
    untested = "未测试"


class DsSyncMode(LabelEnum):
    incremental = "定时增量"
    full = "定时全量"
    cdc = "实时（CDC）"
    manual = "手动"


class DsRunResult(LabelEnum):
    success = "成功"
    delayed = "延迟"
    failed = "失败"


class Datasource(CodeMixin, MutableTableMixin, Base):
    """数据连接（mock DATASOURCES）。code = 前端 id（DS-001…）。

    密码只写不读：password_enc 存 PBKDF2 摘要（EXTENSION POINT：接真实连接时换成可解密的密钥引用）。
    """

    __tablename__ = "datasource"
    __table_args__ = ({"comment": "数据连接（data 模块）"},)

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="名称")
    type: Mapped[DsType] = mapped_column(enum_column(DsType, 16), nullable=False, comment="类型")
    host: Mapped[str] = mapped_column(String(255), nullable=False, comment="主机")
    port: Mapped[int] = mapped_column(Integer, nullable=False, comment="端口")
    database: Mapped[str] = mapped_column(String(128), nullable=False, comment="库名（ERP：应用编码）")
    username: Mapped[str] = mapped_column(String(128), nullable=False, comment="用户名（接口 user）")
    password_enc: Mapped[str | None] = mapped_column(
        String(255), comment="密码摘要（只写不读，接口不返回；seed 行为空）"
    )
    compat_mode: Mapped[str | None] = mapped_column(String(32), comment="达梦兼容模式")
    database_mode: Mapped[str | None] = mapped_column(String(16), comment="金仓 database_mode")
    erp_system: Mapped[str | None] = mapped_column(String(64), comment="ERP 系统")
    app_id: Mapped[str | None] = mapped_column(String(64), comment="ERP appId")
    ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[DsStatus] = mapped_column(enum_column(DsStatus, 16), nullable=False, comment="连接状态")
    last_test: Mapped[dict | None] = mapped_column(JsonType, comment="最近一次测试 {time, ok, ms, msg}")
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="负责人姓名（原文）")
    object_types: Mapped[list[str]] = mapped_column(
        StrArray(64), nullable=False, default=list, comment="映射的对象类型（中文名，mock 原样）"
    )
    sync_mode: Mapped[DsSyncMode] = mapped_column(enum_column(DsSyncMode, 16), nullable=False)
    sync_cron: Mapped[str] = mapped_column(String(64), nullable=False, default="-")
    sync_cron_text: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sync_window: Mapped[str] = mapped_column(String(32), nullable=False, default="全天")
    sync_cursor_field: Mapped[str] = mapped_column(String(64), nullable=False, default="-")
    sync_next_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="下次运行")
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="库表数")
    created_on: Mapped[date] = mapped_column(Date, nullable=False, comment="创建日期（接口 createdAt）")
    note: Mapped[str | None] = mapped_column(Text, comment="详情顶部说明")
    ref_only: Mapped[dict | None] = mapped_column(JsonType, comment="只作参考的对象 {objects, source}")

    runs: Mapped[list["DatasourceRun"]] = relationship(
        back_populates="datasource",
        order_by="DatasourceRun.seq",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class DatasourceRun(IdMixin, AppendOnlyTableMixin, Base):
    """同步运行记录（mock Datasource.runs，DsRun）。seq = 在 runs 数组里的下标（0 = 最新）。"""

    __tablename__ = "datasource_run"
    __table_args__ = (
        UniqueConstraint("datasource_id", "seq"),
        {"comment": "数据连接同步运行记录（data 模块）"},
    )

    datasource_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("datasource.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="展示顺序（0 = 最新）")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="接口 time")
    result: Mapped[DsRunResult] = mapped_column(enum_column(DsRunResult, 16), nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    secs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    msg: Mapped[str] = mapped_column(Text, nullable=False, default="")

    datasource: Mapped[Datasource] = relationship(back_populates="runs")


class DatasourceTable(IdMixin, MutableTableMixin, Base):
    """数据源库表清单（mock dsTables()）。seq = mock 排序（已映射在前，再按 schema+表名）。"""

    __tablename__ = "datasource_table"
    __table_args__ = (
        UniqueConstraint("datasource_id", "schema_name", "name"),
        {"comment": "数据连接的库表清单（data 模块）"},
    )

    datasource_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("datasource.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="展示顺序")
    schema_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="接口 schema")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="表名")
    comment: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="说明")
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="接口 rows")
    col_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="接口 cols")
    object_type: Mapped[str | None] = mapped_column(String(64), comment="映射的对象类型（中文名）")
    ref: Mapped[str | None] = mapped_column(String(255), comment="参考表：以哪张表为准")
    meta_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="接口 updatedAt（源表最近更新）"
    )

class GovernedBinding(IdMixin, Base):
    """Explicitly approved local PostgreSQL table and column binding."""
    __tablename__ = "governed_binding"
    __table_args__ = (UniqueConstraint("object_code"), UniqueConstraint("table_name"))
    object_code: Mapped[str] = mapped_column(String(64), nullable=False)
    table_name: Mapped[str] = mapped_column(String(63), nullable=False)
    read_columns: Mapped[list] = mapped_column(JsonType, nullable=False)
    write_columns: Mapped[list] = mapped_column(JsonType, nullable=False)
    supplementary: Mapped[dict | None] = mapped_column(JsonType, deferred=True)
