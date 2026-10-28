"""Persist the governed field selected by a quality check."""
from alembic import op
import sqlalchemy as sa

revision = "quality_0038_field"
down_revision = "versioning_0037_release_scope"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("quality_check", sa.Column("field", sa.String(64), nullable=True))
    op.add_column("quality_check", sa.Column("format_rule", sa.String(24), nullable=True))

def downgrade():
    raise RuntimeError("质量检查字段不可自动丢弃")
