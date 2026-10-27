"""Append-only delivery attempts and human dispositions; retain all existing data."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "event_0016"
down_revision = "dynamic_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_delivery_record",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("delivery_id", sa.BigInteger(), sa.ForeignKey("event_delivery.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("error", sa.String(128)),
        sa.Column("result", JSONB()),
        sa.Column("detail", JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_event_delivery_record_delivery_id_id", "event_delivery_record", ["delivery_id", "id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM event_delivery_record)")):
        raise RuntimeError("Archive delivery evidence before downgrade")
    op.drop_table("event_delivery_record")
