"""Retain quality definitions and run history when retiring through CR publication."""

import sqlalchemy as sa
from alembic import op

revision = "quality_cr_0011"
down_revision = "event_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quality_check", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM quality_check WHERE retired_at IS NOT NULL)")
    ):
        raise RuntimeError("存在已退休质量检查，不能丢弃退休状态；请先恢复定义或保留迁移")
    op.drop_column("quality_check", "retired_at")
