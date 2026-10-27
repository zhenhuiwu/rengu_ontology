"""Private workspace inbox for explicit CR review submissions; no backfill or external delivery."""

import sqlalchemy as sa
from alembic import op

revision = "notify_0022"
down_revision = "static_0021"
branch_labels = None
depends_on = None
DOMAIN_TABLES = ["notification"]


def upgrade():
    op.create_table(
        "notification",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ontology_id",
            sa.BigInteger(),
            sa.ForeignKey("ontology_workspace.id"),
            nullable=False,
            server_default=sa.text("NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("recipient_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column(
            "source_event_id",
            sa.BigInteger(),
            sa.ForeignKey("cr_event.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cr_id", sa.BigInteger(), sa.ForeignKey("change_request.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("ontology_id", "source_event_id", "recipient_id"),
    )
    op.create_index("ix_notification_ontology_id", "notification", ["ontology_id"])
    op.create_index(
        "ix_notification_recipient_recent",
        "notification",
        ["ontology_id", "recipient_id", "created_at", "id"],
    )
    op.create_index(
        "ix_notification_recipient_unread", "notification", ["ontology_id", "recipient_id", "read_at"]
    )
    for field, parent in [("cr_id", "change_request"), ("source_event_id", "cr_event")]:
        op.execute(f"""
            CREATE TRIGGER trg_notification_{field}_ontology BEFORE INSERT OR UPDATE ON notification
            FOR EACH ROW EXECUTE FUNCTION ontology_reference_guard('{field}','{parent}')
        """)
    op.execute("ALTER TABLE notification ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY ontology_scope ON notification
        USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)
        WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)
    """)
    op.execute("GRANT SELECT,INSERT,UPDATE ON notification TO rengu_ontology_scoped")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE notification_id_seq TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError("Preserve notification/read history; destructive downgrade is intentionally blocked")
