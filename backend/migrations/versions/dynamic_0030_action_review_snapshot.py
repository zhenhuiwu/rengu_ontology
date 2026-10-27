"""Preserve action confirmation disclosures without reconstructing historical records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "dynamic_0030"
down_revision = "admin_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("action_run", sa.Column(
        "review_snapshot", JSONB(), nullable=True,
        comment="暂存时的人工复核项与声明效果；旧记录为空",
    ))


def downgrade():
    raise RuntimeError("Preserve recorded action confirmation disclosures; destructive downgrade is blocked")
