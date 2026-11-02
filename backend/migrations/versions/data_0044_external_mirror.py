"""Workspace-scoped external mirror and progress."""
import sqlalchemy as sa
from alembic import op
from app.db.types import JsonType

revision = "data_0044_external_mirror"
down_revision = "data_0043_external_read"
branch_labels = None
depends_on = None


def upgrade():
    common = lambda: [
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=False,
                  server_default=sa.text("NULLIF(current_setting('rengu.ontology_id', true), '')::bigint")),
        sa.Column("object_code", sa.String(64), nullable=False),
    ]
    op.create_table("external_mirror_state", *common(),
        sa.Column("binding_signature", sa.String(64), nullable=False),
        sa.Column("cursor_value", JsonType), sa.Column("processed", sa.BigInteger(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("error", sa.Text()),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)), sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("ontology_id", "object_code"))
    op.create_table("external_mirror_row", *common(),
        sa.Column("source_key", sa.String(512), nullable=False), sa.Column("binding_signature", sa.String(64), nullable=False),
        sa.Column("payload", JsonType, nullable=False),
        sa.UniqueConstraint("ontology_id", "object_code", "source_key"))
    for name in ("external_mirror_state", "external_mirror_row"):
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY ontology_scope ON {name} USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO rengu_ontology_scoped")
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {name}_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("镜像数据不可自动丢弃")
