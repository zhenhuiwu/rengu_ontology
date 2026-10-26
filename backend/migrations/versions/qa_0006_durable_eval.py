"""Persist evaluation queue identity and execution context.

Revision ID: qa_0006
Revises: data_quality_0006
"""

import sqlalchemy as sa
from alembic import op

from app.db.types import JsonType

revision = "qa_0006"
down_revision = "data_quality_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("qa_eval_run", sa.Column("queue_job_id", sa.BigInteger(), nullable=True))
    op.add_column("qa_eval_run", sa.Column("execution_context", JsonType, nullable=True))
    op.drop_constraint(op.f("ck_qa_eval_run_runstatus"), "qa_eval_run", type_="check")
    op.create_check_constraint(
        op.f("ck_qa_eval_run_runstatus"), "qa_eval_run", "status IN ('queued', 'running', 'done', 'failed')"
    )
    op.create_index(
        "uq_qa_eval_run_active_job",
        "qa_eval_run",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("queue_job_id IS NOT NULL AND status IN ('queued', 'running')"),
    )


def downgrade():
    op.drop_index("uq_qa_eval_run_active_job", table_name="qa_eval_run")
    op.execute("UPDATE qa_eval_run SET status='done' WHERE status='failed'")
    op.drop_constraint(op.f("ck_qa_eval_run_runstatus"), "qa_eval_run", type_="check")
    op.create_check_constraint(
        op.f("ck_qa_eval_run_runstatus"), "qa_eval_run", "status IN ('queued', 'running', 'done')"
    )
    op.drop_column("qa_eval_run", "execution_context")
    op.drop_column("qa_eval_run", "queue_job_id")
