"""integration：外部系统 ext_system（action-event-api §9.1；writeback_call / reconcile_task 在后续迁移）

Revision ID: integration_0001
Revises: 0003
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "integration_0001"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ext_system",
        sa.Column("name", sa.String(length=64), nullable=False, comment="名称（唯一）"),
        sa.Column("vendor", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False, comment="基础地址"),
        sa.Column("auth_type", sa.String(length=64), nullable=False, comment="接口 auth"),
        sa.Column("auth_detail", sa.Text(), nullable=False, comment="脱敏说明（接口 authDetail）"),
        sa.Column("idempotent", sa.Boolean(), nullable=False),
        sa.Column("idem_header", sa.String(length=64), nullable=False, comment="幂等请求头（接口 idemKey）"),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("max_retry", sa.Integer(), nullable=False),
        sa.Column("retry_text", sa.Text(), nullable=False, comment="重试说明原文（接口 retry）"),
        sa.Column("rate_limit", sa.String(length=64), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False, comment="负责人姓名（原文）"),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "degraded",
                "disabled",
                name="extsystemstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("env", sa.String(length=16), nullable=False, comment="环境原文（生产/测试）"),
        sa.Column(
            "usage",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="用途说明 {text, ops, actor?}（mock 原样）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ext_system")),
        sa.UniqueConstraint("code", name=op.f("uq_ext_system_code")),
        sa.UniqueConstraint("name", name=op.f("uq_ext_system_name")),
        comment="外部系统（integration 模块）",
    )


def downgrade() -> None:
    op.drop_table("ext_system")
