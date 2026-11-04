"""Explicit view identities and committed source change images; preserve all existing rows."""

import sqlalchemy as sa
from alembic import op

from app.db.types import JsonType

revision = "data_0052_source_channels"
down_revision = "data_0051_datasource_internal"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("external_read_binding", sa.Column("declared_unique_key", sa.String(128), nullable=True))
    op.create_table(
        "external_cdc_change",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ontology_id",
            sa.BigInteger(),
            sa.ForeignKey("ontology_workspace.id"),
            nullable=False,
            server_default=sa.text("NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"),
        ),
        sa.Column("object_code", sa.String(64), nullable=False),
        sa.Column("event_key", sa.String(512), nullable=False),
        sa.Column("source_position", JsonType, nullable=False),
        sa.Column("operation", sa.String(8), nullable=False),
        sa.Column("before_image", JsonType),
        sa.Column("after_image", JsonType),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ontology_id", "object_code", "event_key"),
    )
    op.create_index(
        "ix_external_cdc_change_ontology_object", "external_cdc_change", ["ontology_id", "object_code", "id"]
    )
    op.create_index("ix_external_cdc_change_ontology_id", "external_cdc_change", ["ontology_id"])
    op.execute("ALTER TABLE external_cdc_change ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE external_cdc_change FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY ontology_scope ON external_cdc_change "
        "USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) "
        "WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)"
    )
    op.execute("GRANT SELECT, INSERT ON external_cdc_change TO rengu_ontology_scoped")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE external_cdc_change_id_seq TO rengu_ontology_scoped")
    op.create_table(
        "source_write_binding",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ontology_id",
            sa.BigInteger(),
            sa.ForeignKey("ontology_workspace.id"),
            nullable=False,
            server_default=sa.text("NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"),
        ),
        sa.Column("sys_id", sa.BigInteger(), sa.ForeignKey("ext_system.id"), nullable=False),
        sa.Column("datasource_code", sa.String(64), nullable=False),
        sa.Column("source_signature", sa.String(64), nullable=False),
        sa.Column("object_code", sa.String(64), nullable=False),
        sa.Column("schema_name", sa.String(128), nullable=False),
        sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("key_column", sa.String(128), nullable=False),
        sa.Column("receipt_table", sa.String(128), nullable=False),
        sa.Column("username", sa.String(128), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("write_columns", JsonType, nullable=False),
        sa.Column("actions", JsonType, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("ontology_id", "sys_id"),
    )
    op.execute("ALTER TABLE source_write_binding ENABLE ROW LEVEL SECURITY")
    op.create_index("ix_source_write_binding_ontology_id", "source_write_binding", ["ontology_id"])
    op.execute("ALTER TABLE source_write_binding FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY ontology_scope ON source_write_binding "
        "USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) "
        "WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON source_write_binding TO rengu_ontology_scoped")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE source_write_binding_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("Source identities and committed change evidence cannot be discarded automatically")
