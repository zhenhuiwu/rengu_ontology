"""Per-key function restrictions; NULL preserves the existing functions:invoke scope contract."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "admin_0029"
down_revision = "notify_0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("api_key", sa.Column("function_types", JSONB(), nullable=True))


def downgrade():
    raise RuntimeError("Preserve function allowlists; removing them would widen existing API keys")
