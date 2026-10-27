"""Preserve CR inbox rows and admit idempotent, source-backed page subscriptions."""

import sqlalchemy as sa
from alembic import op

revision = "notify_0028"
down_revision = "dynamic_0027"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("notification", "source_event_id", nullable=True)
    op.alter_column("notification", "cr_id", nullable=True)
    op.add_column(
        "notification", sa.Column("source_kind", sa.String(16), nullable=False, server_default="cr")
    )
    op.add_column(
        "notification",
        sa.Column("source_outbox_id", sa.BigInteger(), sa.ForeignKey("outbox_event.id", ondelete="CASCADE")),
    )
    op.add_column("notification", sa.Column("target_code", sa.String(64)))
    op.create_unique_constraint(
        "uq_notification_outbox_recipient",
        "notification",
        ["ontology_id", "source_outbox_id", "recipient_id"],
    )
    op.create_check_constraint(
        "notification_source",
        "notification",
        "(source_kind='cr' AND source_event_id IS NOT NULL AND cr_id IS NOT NULL "
        "AND source_outbox_id IS NULL) OR (source_kind='page' AND source_event_id IS NULL "
        "AND cr_id IS NULL AND source_outbox_id IS NOT NULL AND target_code IS NOT NULL)",
    )


def downgrade():
    raise RuntimeError("Preserve subscription notification history; destructive downgrade is blocked")
