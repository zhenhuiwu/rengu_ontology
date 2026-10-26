"""event 模块运行表：event（近期事件）、event_daily_stat（近 14 天趋势）。

前端 EventTypes.vue；action-event-api §9.1 / §12（palantir-r2）。

Revision ID: event_0003
Revises: admin_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "event_0003"
down_revision: str | None = "admin_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event",
        sa.Column("event_type_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, comment="接口 at"),
        sa.Column(
            "obj_kind", sa.String(length=16), nullable=True, comment="store/product/order/action，可空"
        ),
        sa.Column("object_id", sa.String(length=64), nullable=False, comment="接口 objId"),
        sa.Column("obj_label", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, comment="载荷摘要"),
        sa.Column(
            "outcome",
            sa.Enum(
                "delivered",
                "alert_created",
                "deduped",
                "partial",
                "failed",
                name="eventoutcome",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("subscriber_count", sa.Integer(), nullable=False, comment="接口 subscribers"),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="原始载荷（seed 无）",
        ),
        sa.Column("is_test", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["event_type_id"],
            ["event_type.id"],
            name=op.f("fk_event_event_type_id_event_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event")),
        sa.UniqueConstraint("code", name=op.f("uq_event_code")),
        comment="事件（近期事件流）",
    )
    op.create_index(
        "ix_event_event_type_id_occurred_at_id", "event", ["event_type_id", "occurred_at", "id"], unique=False
    )
    op.create_table(
        "event_daily_stat",
        sa.Column("event_type_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["event_type_id"],
            ["event_type.id"],
            name=op.f("fk_event_daily_stat_event_type_id_event_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_daily_stat")),
        sa.UniqueConstraint(
            "event_type_id", "stat_date", name=op.f("uq_event_daily_stat_event_type_id_stat_date")
        ),
        comment="事件类型按天统计",
    )


def downgrade() -> None:
    op.drop_table("event_daily_stat")
    op.drop_index("ix_event_event_type_id_occurred_at_id", table_name="event")
    op.drop_table("event")
