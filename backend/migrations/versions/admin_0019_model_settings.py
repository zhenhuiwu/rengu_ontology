"""Shared platform model settings; existing env configuration remains untouched."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "admin_0019"
down_revision = "ontology_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "platform_setting",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("secret_enc", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON platform_setting TO rengu_ontology_scoped")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE platform_setting_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("Preserve saved model configuration and encrypted credentials; downgrade is blocked")
