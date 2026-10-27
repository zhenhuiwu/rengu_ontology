"""Opt-in rule schedule cursors and durable run history; no seed backfill."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "dynamic_0015"
down_revision = "event_0014"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("rule_id", sa.BigInteger(), sa.ForeignKey("rule.id"), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
    ]


def upgrade():
    op.create_table("rule_schedule_cursor", *common(),
                    sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
                    sa.UniqueConstraint("rule_id"))
    op.create_table("rule_schedule_run", *common(),
                    sa.Column("release_id", sa.BigInteger(), sa.ForeignKey("ontology_release.id")),
                    sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
                    sa.Column("started_at", sa.DateTime(timezone=True)),
                    sa.Column("finished_at", sa.DateTime(timezone=True)),
                    sa.Column("state", sa.String(20), nullable=False),
                    sa.Column("definition", JSONB(), nullable=False),
                    sa.Column("permission_fingerprint", sa.String(64), nullable=False),
                    sa.Column("result", JSONB()), sa.Column("error", sa.Text()),
                    sa.UniqueConstraint("rule_id", "rule_version", "scheduled_at"))
    op.create_index("ix_rule_schedule_run_rule_id", "rule_schedule_run", ["rule_id"])
    op.create_index("ix_rule_schedule_run_release_id", "rule_schedule_run", ["release_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM rule_schedule_run)")):
        raise RuntimeError("Archive schedule history before downgrade")
    op.drop_table("rule_schedule_run")
    op.drop_table("rule_schedule_cursor")
