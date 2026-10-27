"""Independent ontology workspaces, preserved retail data, and enforced transaction RLS.

Migration connection needs CREATEROLE (or superuser); application transactions SET LOCAL ROLE
to the non-login runtime role. No records are deleted, reseeded, or copied into new workspaces.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "ontology_0018"
down_revision = "admin_0017"
branch_labels = None
depends_on = None

DOMAIN_TABLES = [
    "action_run_daily_counter",
    "alert",
    "datasource",
    "event_envelope_field",
    "ext_system",
    "knowledge",
    "metric",
    "object_type",
    "outbox_event",
    "product",
    "qa_cert",
    "qa_eval_question",
    "qa_session_option",
    "qa_synonym_link",
    "qa_template",
    "store",
    "action_run",
    "alert_log",
    "biz_order",
    "datasource_run",
    "datasource_table",
    "dimension",
    "function",
    "knowledge_apply",
    "lifecycle",
    "link_type",
    "object_view",
    "onto_qa_path",
    "ontology_release",
    "property",
    "qa_cert_variant",
    "qa_favorite",
    "qa_freq_question",
    "qa_page",
    "qa_query_log",
    "qa_session",
    "qa_template_card",
    "quality_check",
    "action_run_log",
    "action_type",
    "change_request",
    "dim_level",
    "dim_mapping",
    "dim_value",
    "event_type",
    "fn_param",
    "fn_use",
    "fn_version",
    "lifecycle_draft",
    "lifecycle_version",
    "link_quality",
    "onto_qa_path_example",
    "onto_qa_path_step",
    "order_line",
    "qa_eval_run",
    "qa_object_type_view",
    "qa_page_card",
    "qa_page_share_member",
    "qa_page_subscription",
    "qa_turn",
    "quality_run",
    "reconcile_task",
    "scenario",
    "writeback_call",
    "action_param",
    "action_run_daily_stat",
    "action_type_rule",
    "action_type_version",
    "cr_check",
    "cr_event",
    "cr_impact",
    "cr_item",
    "cr_target",
    "cr_trial",
    "event",
    "event_daily_stat",
    "event_dedup",
    "event_payload_field",
    "event_subscription",
    "event_type_version",
    "lifecycle_state",
    "ontology_edit_rule",
    "qa_eval_fail",
    "qa_feedback",
    "qa_permission_request",
    "qa_turn_event",
    "rule",
    "scenario_impact",
    "scenario_result",
    "side_effect",
    "submission_criterion",
    "event_delivery",
    "lifecycle_transition",
    "rule_draft",
    "rule_hit",
    "rule_schedule_cursor",
    "rule_schedule_run",
    "rule_version",
    "event_delivery_record",
    "lifecycle_change",
]


def upgrade():
    conn = op.get_bind()
    quote = conn.dialect.identifier_preparer.quote
    op.create_table(
        "ontology_workspace",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("domain", sa.String(64), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_table(
        "ontology_member",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("sys_user.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.UniqueConstraint("ontology_id", "user_id"),
        sa.CheckConstraint("role IN ('owner','editor','viewer')", name="ontology_member_role"),
    )
    op.create_table(
        "ontology_role_binding",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=False),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("sys_role.id"), nullable=False),
        sa.UniqueConstraint("ontology_id", "role_id"),
    )
    op.execute(
        "INSERT INTO ontology_workspace (code,name,description,domain,is_default) VALUES "
        "('retail','零售经营本体','既有领域定义、版本和业务记录完整保留于此工作空间','retail',true)"
    )
    conn.execute(
        sa.text("""
        INSERT INTO ontology_member (ontology_id,user_id,role)
        SELECT 1,u.id,CASE WHEN EXISTS (
                SELECT 1 FROM sys_role_member m JOIN sys_role r ON r.id=m.role_id
                WHERE m.user_id=u.id AND r.func->>'releases'='manage') THEN 'owner'
            WHEN EXISTS (SELECT 1 FROM sys_role_member m JOIN sys_role r ON r.id=m.role_id,
                         jsonb_each_text(r.func) f
                         WHERE m.user_id=u.id AND f.value='manage') THEN 'editor'
            ELSE 'viewer' END FROM sys_user u
    """),
    )
    op.execute("INSERT INTO ontology_role_binding (ontology_id,role_id) SELECT 1,id FROM sys_role")
    op.add_column(
        "api_key", sa.Column("ontology_ids", JSONB(), nullable=False, server_default=sa.text("'[1]'::jsonb"))
    )
    op.alter_column("api_key", "ontology_ids", server_default=None)
    op.add_column(
        "audit_log",
        sa.Column("ontology_id", sa.BigInteger(), sa.ForeignKey("ontology_workspace.id"), nullable=True),
    )

    inspector = sa.inspect(conn)
    for table in DOMAIN_TABLES:
        # Constant ADD COLUMN default preserves append-only records without firing UPDATE triggers.
        op.add_column(table, sa.Column("ontology_id", sa.BigInteger(), nullable=False, server_default="1"))
        op.alter_column(
            table,
            "ontology_id",
            server_default=sa.text("NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"),
        )
        op.create_foreign_key(
            f"fk_{table}_ontology_id_ontology_workspace", table, "ontology_workspace", ["ontology_id"], ["id"]
        )
        op.create_index(f"ix_{table}_ontology_id", table, ["ontology_id"])
        for constraint in inspector.get_unique_constraints(table):
            columns = constraint["column_names"]
            op.drop_constraint(constraint["name"], table, type_="unique")
            op.create_unique_constraint(None, table, ["ontology_id", *columns])
        indexes = (
            conn.execute(
                sa.text("""
            SELECT i.indexrelid::regclass::text AS name, pg_get_indexdef(i.indexrelid) AS definition
            FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
            WHERE t.oid=to_regclass(:table) AND i.indisunique AND NOT i.indisprimary
            AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid=i.indexrelid)
        """),
                {"table": table},
            )
            .mappings()
            .all()
        )
        for index in indexes:
            definition = index["definition"]
            if " USING btree (" not in definition:
                raise RuntimeError(f"Review non-btree unique index before scoping: {index['name']}")
            conn.execute(sa.text(f"DROP INDEX {index['name']}"))
            conn.execute(sa.text(definition.replace(" USING btree (", " USING btree (ontology_id, ", 1)))

    op.execute("""
        CREATE FUNCTION ontology_reference_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE referenced_id bigint; parent_ontology bigint;
        BEGIN
            IF TG_OP = 'UPDATE' AND NEW.ontology_id IS DISTINCT FROM OLD.ontology_id THEN
                RAISE EXCEPTION 'Moving records between ontology workspaces is forbidden'
                    USING ERRCODE='23514';
            END IF;
            referenced_id := (to_jsonb(NEW)->>TG_ARGV[0])::bigint;
            IF referenced_id IS NOT NULL THEN
                EXECUTE format('SELECT ontology_id FROM %I WHERE id=$1',TG_ARGV[1])
                    INTO parent_ontology USING referenced_id;
                IF parent_ontology IS NULL OR parent_ontology <> NEW.ontology_id THEN
                    RAISE EXCEPTION 'Ontology reference is unavailable in this workspace'
                        USING ERRCODE='23503';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in DOMAIN_TABLES:
        for fk in inspector.get_foreign_keys(table):
            if fk["referred_table"] not in DOMAIN_TABLES:
                continue
            if len(fk["constrained_columns"]) != 1 or fk["referred_columns"] != ["id"]:
                raise RuntimeError(f"Review composite ontology reference: {table} {fk['name']}")
            column = fk["constrained_columns"][0]
            name = quote(f"trg_{table}_{column}_ontology"[:63])
            conn.execute(
                sa.text(
                    f"CREATE TRIGGER {name} BEFORE INSERT OR UPDATE ON {quote(table)} "
                    f"FOR EACH ROW EXECUTE FUNCTION "
                    f"ontology_reference_guard('{column}','{fk['referred_table']}')"
                )
            )
        # Even rows without domain FKs cannot be moved into another workspace by an UPDATE.
        conn.execute(
            sa.text(
                f"CREATE TRIGGER {quote(('trg_' + table + '_scope_immutable')[:63])} "
                f"BEFORE UPDATE ON {quote(table)} FOR EACH ROW "
                "EXECUTE FUNCTION ontology_reference_guard('id', '" + table + "')"
            )
        )
        op.execute(f"ALTER TABLE {quote(table)} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {quote(table)} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY ontology_scope ON {quote(table)} "
            "USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint) "
            "WITH CHECK (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)"
        )

    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rengu_ontology_scoped') THEN
                CREATE ROLE rengu_ontology_scoped NOLOGIN NOSUPERUSER NOCREATEDB
                    NOCREATEROLE NOINHERIT NOBYPASSRLS;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='rengu_ontology_scoped'
                       AND (rolsuper OR rolbypassrls OR rolcanlogin OR rolcreatedb OR rolcreaterole)) THEN
                RAISE EXCEPTION 'rengu_ontology_scoped must be an unprivileged NOLOGIN role';
            END IF;
            EXECUTE format('GRANT rengu_ontology_scoped TO %I', current_user);
        END $$
    """)
    op.execute("GRANT USAGE ON SCHEMA public TO rengu_ontology_scoped")
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO rengu_ontology_scoped")
    op.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO rengu_ontology_scoped")


def downgrade():
    raise RuntimeError(
        "Workspace isolation downgrade is intentionally blocked: preserve workspace data and authorizations"
    )
