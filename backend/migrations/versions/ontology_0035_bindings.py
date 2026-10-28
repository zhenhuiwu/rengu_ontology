"""Persist workspace-local approved business table bindings."""
import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = "ontology_0035_bindings"
down_revision = "integration_0034"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "governed_binding",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=False,
                  server_default=sa.text("NULLIF(current_setting('rengu.ontology_id', true), '')::bigint")),
        sa.Column("object_code", sa.String(64), nullable=False),
        sa.Column("table_name", sa.String(63), nullable=False),
        sa.Column("read_columns", JsonType, nullable=False),
        sa.Column("write_columns", JsonType, nullable=False),
        sa.UniqueConstraint("ontology_id", "object_code"),
        sa.UniqueConstraint("ontology_id", "table_name"),
    )
    op.execute("ALTER TABLE governed_binding ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE governed_binding FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY ontology_scope ON governed_binding USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON governed_binding TO rengu_ontology_scoped")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE governed_binding_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("受控绑定不可自动丢弃")
