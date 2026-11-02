"""Persist workspace-scoped external object read approvals."""
import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = "data_0043_external_read"
down_revision = "integration_0042_http_endpoints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("external_read_binding",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=False, server_default=sa.text("NULLIF(current_setting('rengu.ontology_id', true), '')::bigint")),
        sa.Column("object_code", sa.String(64), nullable=False), sa.Column("datasource_code", sa.String(64), nullable=False),
        sa.Column("schema_name", sa.String(128), nullable=False), sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("read_columns", JsonType, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)), sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("ontology_id", "object_code"))
    op.execute("ALTER TABLE external_read_binding ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE external_read_binding FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY ontology_scope ON external_read_binding USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON external_read_binding TO rengu_ontology_scoped")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE external_read_binding_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("外部只读授权不可自动丢弃")
