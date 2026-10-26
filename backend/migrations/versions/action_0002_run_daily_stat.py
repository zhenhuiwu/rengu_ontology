"""action 模块：action_run_daily_stat（动作类型近 14 天每日执行量）。

前端 ActionTypes.vue「近 14 天执行」；action-event-api §12（palantir-r2）。

Revision ID: action_0002
Revises: event_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "action_0002"
down_revision: str | None = "event_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_run_daily_stat",
        sa.Column("action_type_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("ok", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("unknown", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_action_run_daily_stat_action_type_id_action_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_run_daily_stat")),
        sa.UniqueConstraint(
            "action_type_id", "stat_date", name=op.f("uq_action_run_daily_stat_action_type_id_stat_date")
        ),
        comment="动作类型每日执行量",
    )


def downgrade() -> None:
    op.drop_table("action_run_daily_stat")
