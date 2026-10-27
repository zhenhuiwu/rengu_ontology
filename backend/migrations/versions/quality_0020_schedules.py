"""Opt-in quality scheduling; retain definitions/results and leave old schedules disarmed."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "quality_0020"
down_revision = "admin_0019"
branch_labels = None
depends_on = None
DOMAIN_TABLES = ["quality_schedule_cursor"]


def upgrade():
    op.create_table(
        "quality_schedule_cursor",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "ontology_id",
            sa.BigInteger(),
            sa.ForeignKey("ontology_workspace.id"),
            nullable=False,
            server_default=sa.text("NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"),
        ),
        sa.Column("check_id", sa.BigInteger(), sa.ForeignKey("quality_check.id"), nullable=False),
        sa.Column("check_version", sa.Integer(), nullable=False),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("permission_fingerprint", sa.String(64), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(200)),
        sa.UniqueConstraint("ontology_id", "check_id"),
    )
    op.create_index("ix_quality_schedule_cursor_ontology_id", "quality_schedule_cursor", ["ontology_id"])
    op.create_index("ix_quality_schedule_cursor_next_at", "quality_schedule_cursor", ["next_at"])
    op.execute("""
        CREATE TRIGGER trg_quality_schedule_cursor_check_id_ontology
        BEFORE INSERT OR UPDATE ON quality_schedule_cursor FOR EACH ROW
        EXECUTE FUNCTION ontology_reference_guard('check_id','quality_check')
    """)
    op.execute("ALTER TABLE quality_schedule_cursor ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE quality_schedule_cursor FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY ontology_scope ON quality_schedule_cursor
        USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)
        WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)
    """)
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON quality_schedule_cursor TO rengu_ontology_scoped")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE quality_schedule_cursor_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("Preserve quality schedule state; destructive downgrade is intentionally blocked")
