"""Persist the explicitly selected read-only integration probe path."""
from alembic import op
import sqlalchemy as sa

revision = "integration_0041_probe_path"
down_revision = "ontology_0040_soft_delete"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ext_system", sa.Column("probe_path", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("ext_system", "probe_path")
