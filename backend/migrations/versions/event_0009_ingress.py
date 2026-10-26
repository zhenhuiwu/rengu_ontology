"""Persist authenticated event ingress and durable deduplication state.

Revision ID: event_0009
Revises: functions_0008
"""

import sqlalchemy as sa
from alembic import op

revision = "event_0009"
down_revision = "functions_0008"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(op.f("ck_event_eventoutcome"), "event", type_="check")
    op.create_check_constraint(
        op.f("ck_event_eventoutcome"),
        "event",
        "outcome IN ('delivered','alert_created','deduped','partial','failed','pending')",
    )
    op.alter_column("event", "obj_kind", type_=sa.String(64), existing_type=sa.String(16))
    op.alter_column("event", "latency_ms", existing_type=sa.Integer(), nullable=True)
    for name, column_type in (
        ("source", sa.String(128)),
        ("producer_type", sa.String(64)),
        ("producer_object_type", sa.String(64)),
        ("trace_id", sa.String(128)),
        ("envelope_hash", sa.String(64)),
        ("ingested_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("event", sa.Column(name, column_type, nullable=True))
    op.create_table(
        "event_dedup",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "event_type_id",
            sa.BigInteger(),
            sa.ForeignKey("event_type.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column("last_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.UniqueConstraint("event_type_id", "dedup_key"),
    )


def downgrade():
    # A pending event cannot honestly be converted into a delivered/failed event.
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM event WHERE outcome='pending' "
            "OR latency_ms IS NULL OR length(obj_kind)>16)"
        )
    ):
        raise RuntimeError("Event ingress cannot fit the old schema; archive it before downgrade")
    op.drop_table("event_dedup")
    for name in (
        "source",
        "producer_type",
        "producer_object_type",
        "trace_id",
        "envelope_hash",
        "ingested_at",
    ):
        op.drop_column("event", name)
    op.drop_constraint(op.f("ck_event_eventoutcome"), "event", type_="check")
    op.create_check_constraint(
        op.f("ck_event_eventoutcome"),
        "event",
        "outcome IN ('delivered','alert_created','deduped','partial','failed')",
    )
    op.alter_column("event", "latency_ms", existing_type=sa.Integer(), nullable=False)
    op.alter_column("event", "obj_kind", type_=sa.String(16), existing_type=sa.String(64))
