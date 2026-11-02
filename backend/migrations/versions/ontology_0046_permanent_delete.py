"""Mark irreversible workspace deletion without removing immutable history."""
from alembic import op
import sqlalchemy as sa

revision = "ontology_0046_permanent_delete"
down_revision = "data_0045_mirror_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ontology_workspace", sa.Column("permanently_deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    raise RuntimeError("永久删除状态不可自动撤销")
