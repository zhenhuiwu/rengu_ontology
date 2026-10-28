"""Make workspace deletion recoverable without deleting business data."""
from alembic import op
import sqlalchemy as sa

revision = "ontology_0040_soft_delete"
down_revision = "ontology_0039_supp_binding"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ontology_workspace", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ontology_workspace", sa.Column("workers_paused", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    raise RuntimeError("已删除工作空间状态不可自动丢弃")
