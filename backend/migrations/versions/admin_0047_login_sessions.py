"""Persistent password credentials, revocable sessions and shared login throttling."""

from alembic import op
import sqlalchemy as sa

revision = "admin_0047_login_sessions"
down_revision = "ontology_0046_permanent_delete"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "auth_credential",
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), primary_key=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
    )
    op.create_table(
        "auth_session",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_session_user_id", "auth_session", ["user_id"])
    op.create_index("ix_auth_session_expires_at", "auth_session", ["expires_at"])
    op.create_table(
        "auth_login_throttle",
        sa.Column("bucket", sa.String(64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON auth_credential,auth_session,auth_login_throttle TO rengu_ontology_scoped")


def downgrade():
    op.drop_table("auth_login_throttle")
    op.drop_table("auth_session")
    op.drop_table("auth_credential")
