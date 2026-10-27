"""Allow code tables without a source object; retain existing rows and constraints."""

import sqlalchemy as sa
from alembic import op

revision = "static_0021"
down_revision = "quality_0020"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("dimension", "object_id", existing_type=sa.BigInteger(), nullable=True)


def downgrade():
    # PostgreSQL rejects this if standalone tables exist, preserving them rather than deleting data.
    op.alter_column("dimension", "object_id", existing_type=sa.BigInteger(), nullable=False)
