"""Persist optional HTTP action field mapping without changing existing definitions."""
import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = "integration_0034"
down_revision = "event_0032"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("action_type", sa.Column("http_mapping", JsonType, nullable=True))

def downgrade():
    op.drop_column("action_type", "http_mapping")
