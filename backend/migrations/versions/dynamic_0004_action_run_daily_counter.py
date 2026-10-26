"""Reserve daily action numbers in independent short transactions.

Revision ID: dynamic_0004
Revises: qa_0005
"""

import sqlalchemy as sa
from alembic import op

revision: str = "dynamic_0004"
down_revision: str | None = "qa_0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "action_run_daily_counter",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("day", sa.String(8), nullable=False),
        sa.Column("last_value", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_run_daily_counter")),
        sa.UniqueConstraint("day", name=op.f("uq_action_run_daily_counter_day")),
    )


def downgrade() -> None:
    op.drop_table("action_run_daily_counter")
