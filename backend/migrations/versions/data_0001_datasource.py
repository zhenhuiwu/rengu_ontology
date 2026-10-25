"""数据连接（data 模块，api-index §3.1）：datasource / datasource_run / datasource_table。

- 密码只存摘要 password_enc，接口不返回；同步记录、库表清单随数据源级联删除。
- 合并时接在当时的 head 后面（CONVENTIONS §16 线性单 head）：合并顺序里应改为 integration_0002。

Revision ID: data_0001
Revises: event_0002
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "data_0001"
down_revision: str | None = "event_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "datasource",
        sa.Column("name", sa.String(length=128), nullable=False, comment="名称"),
        sa.Column(
            "type",
            sa.Enum(
                "postgresql",
                "dm8",
                "kingbase",
                "mysql",
                "erp",
                name="dstype",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
            comment="类型",
        ),
        sa.Column("host", sa.String(length=255), nullable=False, comment="主机"),
        sa.Column("port", sa.Integer(), nullable=False, comment="端口"),
        sa.Column("database", sa.String(length=128), nullable=False, comment="库名（ERP：应用编码）"),
        sa.Column("username", sa.String(length=128), nullable=False, comment="用户名（接口 user）"),
        sa.Column(
            "password_enc",
            sa.String(length=255),
            nullable=True,
            comment="密码摘要（只写不读，接口不返回；seed 行为空）",
        ),
        sa.Column("compat_mode", sa.String(length=32), nullable=True, comment="达梦兼容模式"),
        sa.Column("database_mode", sa.String(length=16), nullable=True, comment="金仓 database_mode"),
        sa.Column("erp_system", sa.String(length=64), nullable=True, comment="ERP 系统"),
        sa.Column("app_id", sa.String(length=64), nullable=True, comment="ERP appId"),
        sa.Column("ssl", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "error",
                "untested",
                name="dsstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
            comment="连接状态",
        ),
        sa.Column(
            "last_test",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="最近一次测试 {time, ok, ms, msg}",
        ),
        sa.Column("owner", sa.String(length=64), nullable=False, comment="负责人姓名（原文）"),
        sa.Column(
            "object_types",
            sa.JSON().with_variant(postgresql.ARRAY(sa.String(length=64)), "postgresql"),
            nullable=False,
            comment="映射的对象类型（中文名，mock 原样）",
        ),
        sa.Column(
            "sync_mode",
            sa.Enum(
                "incremental",
                "full",
                "cdc",
                "manual",
                name="dssyncmode",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("sync_cron", sa.String(length=64), nullable=False),
        sa.Column("sync_cron_text", sa.String(length=64), nullable=False),
        sa.Column("sync_window", sa.String(length=32), nullable=False),
        sa.Column("sync_cursor_field", sa.String(length=64), nullable=False),
        sa.Column("sync_next_run", sa.DateTime(timezone=True), nullable=True, comment="下次运行"),
        sa.Column("table_count", sa.Integer(), nullable=False, comment="库表数"),
        sa.Column("created_on", sa.Date(), nullable=False, comment="创建日期（接口 createdAt）"),
        sa.Column("note", sa.Text(), nullable=True, comment="详情顶部说明"),
        sa.Column(
            "ref_only",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="只作参考的对象 {objects, source}",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasource")),
        sa.UniqueConstraint("code", name=op.f("uq_datasource_code")),
        comment="数据连接（data 模块）",
    )
    op.create_table(
        "datasource_run",
        sa.Column("datasource_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, comment="展示顺序（0 = 最新）"),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False, comment="接口 time"),
        sa.Column(
            "result",
            sa.Enum(
                "success",
                "delayed",
                "failed",
                name="dsrunresult",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("secs", sa.Integer(), nullable=False),
        sa.Column("msg", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["datasource.id"],
            name=op.f("fk_datasource_run_datasource_id_datasource"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasource_run")),
        sa.UniqueConstraint("datasource_id", "seq", name=op.f("uq_datasource_run_datasource_id_seq")),
        comment="数据连接同步运行记录（data 模块）",
    )
    op.create_index(
        op.f("ix_datasource_run_datasource_id"), "datasource_run", ["datasource_id"], unique=False
    )
    op.create_table(
        "datasource_table",
        sa.Column("datasource_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, comment="展示顺序"),
        sa.Column("schema_name", sa.String(length=64), nullable=False, comment="接口 schema"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="表名"),
        sa.Column("comment", sa.String(length=255), nullable=False, comment="说明"),
        sa.Column("row_count", sa.BigInteger(), nullable=False, comment="接口 rows"),
        sa.Column("col_count", sa.Integer(), nullable=False, comment="接口 cols"),
        sa.Column("object_type", sa.String(length=64), nullable=True, comment="映射的对象类型（中文名）"),
        sa.Column("ref", sa.String(length=255), nullable=True, comment="参考表：以哪张表为准"),
        sa.Column(
            "meta_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="接口 updatedAt（源表最近更新）",
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["datasource.id"],
            name=op.f("fk_datasource_table_datasource_id_datasource"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_datasource_table")),
        sa.UniqueConstraint(
            "datasource_id",
            "schema_name",
            "name",
            name=op.f("uq_datasource_table_datasource_id_schema_name_name"),
        ),
        comment="数据连接的库表清单（data 模块）",
    )
    op.create_index(
        op.f("ix_datasource_table_datasource_id"), "datasource_table", ["datasource_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_datasource_table_datasource_id"), table_name="datasource_table")
    op.drop_table("datasource_table")
    op.drop_index(op.f("ix_datasource_run_datasource_id"), table_name="datasource_run")
    op.drop_table("datasource_run")
    op.drop_table("datasource")
