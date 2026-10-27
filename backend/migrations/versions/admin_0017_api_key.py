"""API keys and real call records; no credential seeds or existing-data changes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "admin_0017"
down_revision = "event_0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_key",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(64), unique=True, nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("env", sa.String(8), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("last4", sa.String(4), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False),
        sa.Column("func_snapshot", JSONB(), nullable=False),
        sa.Column("ip_allow", JSONB(), nullable=False),
        sa.Column("note", sa.String(512), nullable=False),
        sa.Column("creator", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_index("uq_api_key_live_name", "api_key", ["name"], unique=True, postgresql_where=sa.text("revoked_at IS NULL"))
    op.create_index("ix_api_key_expires_at", "api_key", ["expires_at"])
    op.create_table(
        "api_key_call",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("key_id", sa.BigInteger(), sa.ForeignKey("api_key.id"), nullable=False),
        sa.Column("api", sa.String(256), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_api_key_call_key_id_created_at_id", "api_key_call", ["key_id", "created_at", "id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM api_key)")):
        raise RuntimeError("Archive API key and call records before downgrade")
    op.drop_table("api_key_call")
    op.drop_table("api_key")
