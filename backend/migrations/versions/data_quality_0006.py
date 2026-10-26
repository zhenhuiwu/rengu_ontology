"""Persist quality definitions and actual runs, without demo history.
Revision ID: data_quality_0006
Revises: admin_0005
"""

import sqlalchemy as sa
from alembic import op

from app.db.ddl import disable_append_only, enable_append_only
from app.db.types import JsonType

revision = "data_quality_0006"
down_revision = "admin_0005"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
    ]


def upgrade():
    op.create_table(
        "quality_check",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True, comment="业务编号（前端 id）"),
        sa.Column("object_type_id", sa.BigInteger(), sa.ForeignKey("object_type.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("threshold", sa.String(128), nullable=False),
        sa.Column("limit_value", sa.Float(), nullable=False),
        sa.Column("schedule", sa.String(32), nullable=False),
        *common(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_table(
        "quality_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("check_id", sa.BigInteger(), sa.ForeignKey("quality_check.id"), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(24), nullable=False),
        sa.Column("actual", sa.Text(), nullable=False),
        sa.Column("ms", sa.Integer(), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_time", sa.DateTime(timezone=True)),
        sa.Column("definition", JsonType, nullable=False),
        *common(),
    )
    op.create_index("ix_quality_run_check_id", "quality_run", ["check_id"])
    enable_append_only("quality_run")


def downgrade():
    disable_append_only("quality_run")
    op.drop_table("quality_run")
    op.drop_table("quality_check")
