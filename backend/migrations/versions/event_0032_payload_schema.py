"""Persist optional nested event payload definitions without changing existing rows."""
import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = "event_0032"
down_revision = "integration_0031"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("event_payload_field", sa.Column("schema", JsonType, nullable=True))

def downgrade():
    op.drop_column("event_payload_field", "schema")
