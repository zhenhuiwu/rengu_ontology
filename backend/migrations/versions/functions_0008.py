"""Persist function definitions and append-only function versions.

Revision ID: functions_0008
Revises: palantir_0007
"""

import sqlalchemy as sa
from alembic import op

from app.db.ddl import disable_append_only, enable_append_only
from app.db.types import JsonType

revision = "functions_0008"
down_revision = "palantir_0007"
branch_labels = None
depends_on = None


def common():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
    ]


def mutable():
    return [
        *common(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
    ]


def upgrade():
    op.create_table(
        "function",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False, comment="业务编号（前端 id）"),
        sa.Column("name", sa.String(64), unique=True, nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("runtime", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("timeout_s", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("code_text", sa.Text(), nullable=False),
        sa.Column("sample_in", JsonType, nullable=False),
        sa.Column("sample_out", JsonType, nullable=True),
        sa.Column("avg_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calls_7d", sa.Integer(), nullable=False, server_default="0"),
        *mutable(),
    )
    op.create_table(
        "fn_param",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("function_id", sa.BigInteger(), sa.ForeignKey("function.id"), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("type", sa.String(128), nullable=False),
        sa.Column("obj_type", sa.String(128)),
        sa.Column("desc", sa.Text(), nullable=False),
        sa.UniqueConstraint("function_id", "direction", "seq"),
        *mutable(),
    )
    op.create_table(
        "fn_use",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("function_id", sa.BigInteger(), sa.ForeignKey("function.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        *mutable(),
    )
    op.create_table(
        "fn_version",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("function_id", sa.BigInteger(), sa.ForeignKey("function.id"), nullable=False),
        sa.Column("ver", sa.String(32), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("author_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("snapshot", JsonType, nullable=False),
        sa.UniqueConstraint("function_id", "ver"),
        *common(),
    )
    for table in ("fn_param", "fn_use", "fn_version"):
        op.create_index(f"ix_{table}_function_id", table, ["function_id"])
    for sql in enable_append_only("fn_version"):
        op.execute(sql)


def downgrade():
    for sql in disable_append_only("fn_version"):
        op.execute(sql)
    for table in ("fn_version", "fn_use", "fn_param", "function"):
        op.drop_table(table)
