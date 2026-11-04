"""Explicit governance policy and stable relation paths, preserving existing rows."""

import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = "admin_0053_governance_policy"
down_revision = "data_0052_source_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("governance_policy",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=False,
                  server_default=sa.text("NULLIF(current_setting('rengu.ontology_id',true),'')::bigint")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("definition", JsonType, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("ontology_id"))
    op.execute("ALTER TABLE governance_policy ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE governance_policy FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY ontology_scope ON governance_policy USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON governance_policy TO rengu_ontology_scoped")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE governance_policy_id_seq TO rengu_ontology_scoped")
    op.alter_column("sys_role_scope", "prop", type_=sa.String(384), existing_type=sa.String(64))
    op.add_column("action_run", sa.Column("data_purpose", sa.String(64), nullable=True,
        comment="提交时显式声明的数据用途，执行时重新授权"))


def downgrade():
    raise RuntimeError("Governance decisions and existing scopes must be preserved; downgrade requires manual review")
