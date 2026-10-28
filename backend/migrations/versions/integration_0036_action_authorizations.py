"""Persist explicit HTTP action authorizations."""
from alembic import op
from app.db.types import JsonType
import sqlalchemy as sa

revision = "integration_0036_action_auth"
down_revision = "ontology_0035_bindings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ext_system", sa.Column("action_authorizations", JsonType, nullable=True))


def downgrade():
    raise RuntimeError("动作授权不可自动丢弃")
