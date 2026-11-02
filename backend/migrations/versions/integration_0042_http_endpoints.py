"""Persist endpoint catalog and action selection."""
import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = "integration_0042_http_endpoints"
down_revision = "integration_0041_probe_path"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("ext_system", sa.Column("http_endpoints", JsonType, nullable=True))
    op.add_column("action_type", sa.Column("http_endpoint_id", sa.String(64), nullable=True))

def downgrade():
    raise RuntimeError("接口绑定不可自动丢弃")
