"""Distinguish formal publications from internal and historical snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "versioning_0037_release_scope"
down_revision = "integration_0036_action_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ontology_release", sa.Column("scope", sa.String(16), nullable=False, server_default="legacy"))
    op.create_check_constraint("ck_ontology_release_scope", "ontology_release", "scope IN ('legacy', 'working', 'formal')")


def downgrade():
    raise RuntimeError("发布类型不可自动丢弃")
