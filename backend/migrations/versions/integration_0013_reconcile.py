"""Durable reconciliation state, preserving historical unknown runs as manual."""

import sqlalchemy as sa
from alembic import op

revision = "integration_0013"
down_revision = "static_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reconcile_task",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("action_run_id", sa.BigInteger(), sa.ForeignKey("action_run.id"), nullable=False),
        sa.Column("sys_id", sa.BigInteger(), sa.ForeignKey("ext_system.id"), nullable=False),
        sa.Column("idem_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("tries", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True)),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("last_ext_status", sa.String(32)),
        sa.Column("last_error", sa.String(128)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("reconciler", sa.String(64)),
        sa.Column("reconcile_result", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("action_run_id"),
        sa.CheckConstraint("state IN ('querying','resolved','manual')", name="reconcilestate"),
    )
    op.create_index("ix_reconcile_task_state_next_at", "reconcile_task", ["state", "next_at"])
    op.execute("""INSERT INTO reconcile_task(action_run_id, sys_id, idem_key, state, round, tries, manual, is_seed, last_error)
                  SELECT r.id, s.id, r.idem_key, 'manual', 1, CASE WHEN r.is_seed THEN 3 ELSE 0 END, true, r.is_seed,
                         CASE WHEN r.is_seed THEN NULL ELSE 'LEGACY_HISTORY_REQUIRES_MANUAL_REVIEW' END
                  FROM action_run r JOIN ext_system s ON s.code=r.sys_code WHERE r.status='unknown'""")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM reconcile_task WHERE NOT is_seed)")):
        raise RuntimeError("Archive non-seed reconciliation history before downgrade")
    op.drop_table("reconcile_task")
