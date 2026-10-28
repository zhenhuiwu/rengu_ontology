"""Store explicit one-to-one supplementary table bindings."""
from alembic import op
from app.db.types import JsonType
import sqlalchemy as sa

revision = "ontology_0039_supp_binding"
down_revision = "quality_0038_field"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("governed_binding", sa.Column("supplementary", JsonType, nullable=True))


def downgrade():
    raise RuntimeError("补充表绑定不可自动丢弃")
