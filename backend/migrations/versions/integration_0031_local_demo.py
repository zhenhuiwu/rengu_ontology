"""Persist the server-controlled local-only flag without reclassifying existing systems."""

import sqlalchemy as sa
from alembic import op

revision = "integration_0031"
down_revision = "qa_0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ext_system", sa.Column("local_only", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    raise RuntimeError("本机演示标记不可丢弃，否则关闭后的目标可能恢复对外发送")
