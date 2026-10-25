"""变更单与发布链路：change_request（含 CRDetail jsonb）/ cr_event（追加评审记录）[推断]。

cr_item / cr_target / cr_check / cr_impact / cr_trial 拆表放第二期（static-api.md §1）。

Revision ID: static_0002
Revises: event_0002
Create Date: 2026-10-26 00:15:30.061608+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "static_0002"
down_revision: str | None = "event_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_request",
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("reviewer", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "in_review",
                "approved",
                "rejected",
                "published",
                name="crstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "level",
            sa.Enum(
                "major",
                "minor",
                "patch",
                name="crlevel",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("changes", sa.Integer(), nullable=False),
        sa.Column("reject_reason", sa.String(length=128), nullable=True),
        sa.Column("release_id", sa.BigInteger(), nullable=True, comment="进入的版本（发布 / 回滚后写）"),
        sa.Column(
            "detail",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="[推断] CRDetail 原样（checks/touches/synonyms…）",
        ),
        sa.Column("status_at", sa.DateTime(timezone=True), nullable=False, comment="mock updatedAt"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["ontology_release.id"],
            name=op.f("fk_change_request_release_id_ontology_release"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_request")),
        sa.UniqueConstraint("code", name=op.f("uq_change_request_code")),
        comment="变更单",
    )
    op.create_index(op.f("ix_change_request_release_id"), "change_request", ["release_id"], unique=False)
    op.create_table(
        "cr_event",
        sa.Column("cr_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False, comment="publish|rollback|…"),
        sa.Column("actor", sa.String(length=64), nullable=False, comment="操作人姓名"),
        sa.Column("actor_code", sa.String(length=32), nullable=True, comment="操作人 sys_user.code"),
        sa.Column("text", sa.String(length=256), nullable=False),
        sa.Column("desc", sa.Text(), nullable=True),
        sa.Column(
            "level", sa.String(length=16), nullable=True, comment="前端 ReviewEvent.status：warn / error"
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["cr_id"],
            ["change_request.id"],
            name=op.f("fk_cr_event_cr_id_change_request"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cr_event")),
        comment="变更单评审记录（只追加）",
    )
    op.create_index(op.f("ix_cr_event_cr_id"), "cr_event", ["cr_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cr_event_cr_id"), table_name="cr_event")
    op.drop_table("cr_event")
    op.drop_index(op.f("ix_change_request_release_id"), table_name="change_request")
    op.drop_table("change_request")
