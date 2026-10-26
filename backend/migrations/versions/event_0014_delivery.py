"""Immutable ingress definitions and durable per-subscriber delivery."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "event_0014"
down_revision = "integration_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("event", sa.Column("definition_snapshot", JSONB()))
    op.create_table(
        "event_delivery",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("event.id"), nullable=False),
        sa.Column("subscriber_name", sa.String(128), nullable=False),
        sa.Column("subscription", JSONB(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String(128)),
        sa.Column("result", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("event_id", "subscriber_name"),
    )
    op.create_index("ix_event_delivery_state_next_at", "event_delivery", ["state", "next_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM event_delivery)")):
        raise RuntimeError("Archive delivery history before downgrade")
    op.drop_table("event_delivery")
    op.drop_column("event", "definition_snapshot")
