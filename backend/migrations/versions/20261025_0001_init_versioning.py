"""初始化：pgvector 扩展 + 本体版本表 ont_release

Revision ID: 0001
Revises:
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "ont_release",
        sa.Column("ver", sa.String(32), nullable=False, comment="版本号 vYYYY.MM.N"),
        sa.Column("parent_ver", sa.String(32), nullable=True, comment="上一版"),
        sa.Column(
            "kind",
            sa.Enum("publish", "rollback", name="releasekind", native_enum=False, length=16, create_constraint=True),
            server_default="publish",
            nullable=False,
        ),
        sa.Column(
            "rollback_of",
            sa.String(32),
            nullable=True,
            comment="回滚生成的版本：被回滚掉的版本号（内容恢复为它的上一版）",
        ),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False, comment="整版定义快照"),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("change_order_ids", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("change_summary", postgresql.JSONB(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, comment="发布说明（页面显示）"),
        sa.Column("published_by", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.CheckConstraint(r"ver ~ '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$'", name=op.f("ck_ont_release_ver_format")),
        sa.PrimaryKeyConstraint("ver", name=op.f("pk_ont_release")),
        comment="本体整版发布（只追加）",
    )
    op.create_index(op.f("ix_ont_release_published_at"), "ont_release", ["published_at"])
    # 只追加：禁止删除和真正改变内容的 UPDATE（数据库层兜底）。
    # 内容完全相同的 UPDATE 放行，这样 seed 的 UPSERT 可以重复执行（幂等）。
    op.execute(
        """
        CREATE FUNCTION ont_release_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND NEW IS NOT DISTINCT FROM OLD THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'ont_release 只追加，不允许 %', TG_OP USING ERRCODE = 'raise_exception';
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_ont_release_immutable BEFORE UPDATE OR DELETE ON ont_release "
        "FOR EACH ROW EXECUTE FUNCTION ont_release_immutable()"
    )

def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_ont_release_immutable ON ont_release")
    op.execute("DROP FUNCTION IF EXISTS ont_release_immutable()")
    op.drop_index(op.f("ix_ont_release_published_at"), table_name="ont_release")
    op.drop_table("ont_release")
