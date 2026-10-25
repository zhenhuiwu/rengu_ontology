"""integration：外部系统调用记录 writeback_call（action-event-api §9.1、§11.4）。

合并顺序：static_0002（接 event_0002）→ dynamic_0002（接 static_0002）→ 本迁移接 dynamic_0002。
单独合本分支时把本提交回退即可（回到 event_0002）。

Revision ID: integration_0002
Revises: dynamic_0002
Create Date: 2026-10-26 00:37:15.705174+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "integration_0002"
down_revision: str | None = "dynamic_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "writeback_call",
        sa.Column("sys_id", sa.BigInteger(), nullable=False),
        sa.Column("action_run_id", sa.BigInteger(), nullable=True, comment="执行记录（接口 runId）"),
        sa.Column(
            "kind",
            sa.Enum(
                "write",
                "query",
                "test",
                name="callkind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, comment="调用时间（接口 time）"),
        sa.Column("op_name", sa.String(length=64), nullable=False, comment="接口 action"),
        sa.Column("target_label", sa.String(length=200), nullable=False, comment="接口 target"),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("idem_key", sa.String(length=128), nullable=True, comment="幂等键；不支持幂等为空"),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, comment="接口 ms"),
        sa.Column("http_status", sa.Integer(), nullable=True, comment="接口 http；超时为空"),
        sa.Column(
            "result",
            sa.Enum(
                "succeeded",
                "failed",
                "unknown",
                name="callresult",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False, comment="发起人原文"),
        sa.Column("msg", sa.Text(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_run_id"], ["action_run.id"], name=op.f("fk_writeback_call_action_run_id_action_run")
        ),
        sa.ForeignKeyConstraint(
            ["sys_id"], ["ext_system.id"], name=op.f("fk_writeback_call_sys_id_ext_system")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_writeback_call")),
        sa.UniqueConstraint("code", name=op.f("uq_writeback_call_code")),
        comment="外部系统调用记录（integration 模块）",
    )
    op.create_index(
        op.f("ix_writeback_call_action_run_id"), "writeback_call", ["action_run_id"], unique=False
    )
    op.create_index("ix_writeback_call_sys_at", "writeback_call", ["sys_id", "at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_writeback_call_sys_at", table_name="writeback_call")
    op.drop_index(op.f("ix_writeback_call_action_run_id"), table_name="writeback_call")
    op.drop_table("writeback_call")
