"""Persistent per-execution external mirror history."""
import sqlalchemy as sa
from alembic import op

revision = "data_0045_mirror_runs"
down_revision = "data_0044_external_mirror"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("datasource", sa.Column("mirror_schedule_actor", sa.String(64)))
    op.add_column("external_mirror_state", sa.Column("active_run_id", sa.BigInteger()))
    op.create_table("external_mirror_run",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=False,
                  server_default=sa.text("NULLIF(current_setting('rengu.ontology_id', true), '')::bigint")),
        sa.Column("object_code", sa.String(64), nullable=False), sa.Column("datasource_code", sa.String(64), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("read_count", sa.BigInteger(), nullable=False), sa.Column("write_count", sa.BigInteger(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False), sa.Column("error", sa.Text()), sa.Column("actor", sa.String(64), nullable=False))
    op.create_index("ix_external_mirror_run_lookup", "external_mirror_run", ["ontology_id", "object_code", "id"])
    op.execute("ALTER TABLE external_mirror_run ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE external_mirror_run FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY ontology_scope ON external_mirror_run USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON external_mirror_run TO rengu_ontology_scoped")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE external_mirror_run_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("镜像运行历史不可自动丢弃")
