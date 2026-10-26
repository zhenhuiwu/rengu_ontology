"""Record the process that owns an evaluation before background dispatch.

Revision ID: qa_0005
Revises: qa_0004
"""

import sqlalchemy as sa
from alembic import op

revision: str = "qa_0005"
down_revision: str | None = "qa_0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("qa_eval_run", sa.Column("worker_id", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("qa_eval_run", "worker_id")
