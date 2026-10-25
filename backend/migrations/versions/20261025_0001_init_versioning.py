"""初始化：pgvector 扩展 + 只追加表的通用触发器函数 + 本体版本表 ontology_release

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.ddl import GUARD_FUNCTION, append_only_guard_function, disable_append_only, enable_append_only

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(append_only_guard_function())
    op.create_table(
        "ontology_release",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("ver", sa.String(32), nullable=False, comment="版本号 vYYYY.MM.N（业务键，唯一）"),
        sa.Column("parent_id", sa.BigInteger(), nullable=True, comment="上一版（ontology_release.id）"),
        sa.Column(
            "kind",
            sa.Enum("publish", "rollback", name="releasekind", native_enum=False, length=16, create_constraint=True),
            server_default="publish",
            nullable=False,
        ),
        sa.Column(
            "rollback_of_id",
            sa.BigInteger(),
            nullable=True,
            comment="回滚生成的版本：被回滚的版本（ontology_release.id；新版本内容 = 它的上一版）",
        ),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, comment="整版定义快照（各模块组装，版本层不解析）"),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("change_order_ids", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("change_summary", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, comment="发布说明（页面显示）"),
        sa.Column("published_by", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.CheckConstraint(r"ver ~ '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$'", name=op.f("ck_ontology_release_ver_format")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_release")),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["ontology_release.id"], name=op.f("fk_ontology_release_parent_id_ontology_release")
        ),
        sa.ForeignKeyConstraint(
            ["rollback_of_id"],
            ["ontology_release.id"],
            name=op.f("fk_ontology_release_rollback_of_id_ontology_release"),
        ),
        sa.UniqueConstraint("ver", name=op.f("uq_ontology_release_ver")),
        comment="本体整版发布（只追加）",
    )
    op.create_index(op.f("ix_ontology_release_published_at"), "ontology_release", ["published_at"])
    for sql in enable_append_only("ontology_release"):
        op.execute(sql)


def downgrade() -> None:
    for sql in disable_append_only("ontology_release"):
        op.execute(sql)
    op.drop_index(op.f("ix_ontology_release_published_at"), table_name="ontology_release")
    op.drop_table("ontology_release")
    op.execute(f"DROP FUNCTION IF EXISTS {GUARD_FUNCTION}()")
