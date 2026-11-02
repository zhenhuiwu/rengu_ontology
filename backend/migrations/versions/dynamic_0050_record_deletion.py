"""Persistent user deletion; audit logs, canonical references and idempotency remain intact."""
from alembic import op
import sqlalchemy as sa

revision = "dynamic_0050_record_deletion"
down_revision = "data_0049_external_cdc"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("action_run", "alert"):
        op.add_column(name, sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(name, sa.Column("deleted_by", sa.String(64), nullable=True))


def downgrade():
    for name in ("alert", "action_run"):
        op.drop_column(name, "deleted_by")
        op.drop_column(name, "deleted_at")
