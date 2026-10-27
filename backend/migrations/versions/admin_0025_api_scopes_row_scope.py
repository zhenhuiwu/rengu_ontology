"""API key scopes, service accounts, call quota, caller audit columns; per-ontology row scopes.

- api_key: principal_kind user/service, service_account_id, action/event allowlists, rate/quota.
  Existing "元数据读取" scopes become "meta:read" (same metadata-only behaviour).
- service_account (shared platform identity) bound to an existing sys_role; api_key_usage counters.
- audit_log: principal_kind / service_account_code / api_key_prefix for machine callers.
- sys_role_scope becomes an ontology domain table (FORCE RLS). Existing rows are backfilled to
  ontology 1 and mapped to object_type by display name / api name / code / role objType label;
  ambiguous or unknown labels stay unmapped and keep the fail-closed legacy name matching.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "admin_0025"
down_revision = "dynamic_0024"
branch_labels = None
depends_on = None
DOMAIN_TABLES = ["sys_role_scope"]
GUC = "NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"


# Keep the schema in sync with the model comments (alembic check / metadata roundtrip).
COLUMN_COMMENTS = (
    ("service_account", "code", "业务编号（前端 id）"),
    ("api_key_usage", "period", "minute / day"),
    ("audit_log", "principal_kind", "user / service（API 密钥调用）"),
    ("audit_log", "service_account_code", "服务账号编号（SA-0001）"),
    ("audit_log", "api_key_prefix", "调用密钥前缀（不含密钥）"),
    ("sys_role_scope", "object_type_id", "对象类型（稳定引用）"),
    ("sys_role_scope", "object_type_code", "对象类型编号（写入时快照）"),
)


def upgrade():
    op.create_table(
        "service_account",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.String(512), nullable=False, server_default=""),
        sa.Column("role_id", sa.BigInteger(), sa.ForeignKey("sys_role.id"), nullable=False),
        sa.Column("creator", sa.String(128), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        comment="服务账号",
    )
    op.add_column(
        "api_key", sa.Column("principal_kind", sa.String(16), nullable=False, server_default="user")
    )
    op.add_column("api_key", sa.Column("service_account_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_api_key_service_account_id_service_account",
        "api_key",
        "service_account",
        ["service_account_id"],
        ["id"],
    )
    op.alter_column("api_key", "user_id", nullable=True)
    for column in ("action_types", "event_types"):
        op.add_column("api_key", sa.Column(column, JSONB()))
    for column in ("rate_per_minute", "quota_per_day"):
        op.add_column("api_key", sa.Column(column, sa.Integer()))
    op.create_check_constraint(
        "principal",
        "api_key",
        "(principal_kind = 'user' AND user_id IS NOT NULL AND service_account_id IS NULL) OR "
        "(principal_kind = 'service' AND service_account_id IS NOT NULL AND user_id IS NULL)",
    )
    op.execute("""
        UPDATE api_key SET scopes = (
            SELECT COALESCE(
                jsonb_agg(DISTINCT CASE WHEN value = '元数据读取' THEN 'meta:read' ELSE value END), '[]'
            )
            FROM jsonb_array_elements_text(scopes) AS value
        ) WHERE jsonb_typeof(scopes) = 'array'
    """)
    op.create_table(
        "api_key_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("key_id", sa.BigInteger(), sa.ForeignKey("api_key.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period", sa.String(8), nullable=False),
        sa.Column("bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.UniqueConstraint("key_id", "period", "bucket"),
    )
    for column, kind in (("principal_kind", 16), ("service_account_code", 64), ("api_key_prefix", 16)):
        op.add_column("audit_log", sa.Column(column, sa.String(kind)))

    # ---- sys_role_scope → per-ontology domain table ----
    op.add_column("sys_role_scope", sa.Column("ontology_id", sa.BigInteger()))
    op.execute("UPDATE sys_role_scope SET ontology_id = 1")
    op.alter_column("sys_role_scope", "ontology_id", nullable=False, server_default=sa.text(GUC))
    op.create_foreign_key(
        "fk_sys_role_scope_ontology_id_ontology_workspace",
        "sys_role_scope",
        "ontology_workspace",
        ["ontology_id"],
        ["id"],
    )
    op.create_index("ix_sys_role_scope_ontology_id", "sys_role_scope", ["ontology_id"])
    op.drop_constraint("uq_sys_role_scope_code", "sys_role_scope", type_="unique")
    op.create_unique_constraint(
        "uq_sys_role_scope_ontology_id_code", "sys_role_scope", ["ontology_id", "code"]
    )
    op.add_column("sys_role_scope", sa.Column("object_type_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_sys_role_scope_object_type_id_object_type",
        "sys_role_scope",
        "object_type",
        ["object_type_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("sys_role_scope", sa.Column("object_type_code", sa.String(64)))
    op.execute("""
        WITH candidate AS (
            SELECT s.id AS scope_id, o.id AS object_id, o.code AS object_code
            FROM sys_role_scope s
            JOIN sys_role r ON r.id = s.role_id
            JOIN object_type o ON o.ontology_id = s.ontology_id AND (
                o.display_name = s.obj_type OR o.api_name = s.obj_type OR o.code = s.obj_type
                OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(r.objs) = 'array' THEN r.objs ELSE '[]'::jsonb END
                    ) AS entry
                    WHERE entry->>'objType' = s.obj_type AND entry->>'api' = o.api_name
                )
            )
        ), unique_match AS (
            SELECT scope_id, min(object_id) AS object_id, min(object_code) AS object_code
            FROM candidate GROUP BY scope_id HAVING count(DISTINCT object_id) = 1
        )
        UPDATE sys_role_scope s SET object_type_id = u.object_id, object_type_code = u.object_code
        FROM unique_match u WHERE s.id = u.scope_id
    """)
    op.execute("""
        CREATE TRIGGER trg_sys_role_scope_object_type_id_ontology BEFORE INSERT OR UPDATE ON sys_role_scope
        FOR EACH ROW EXECUTE FUNCTION ontology_reference_guard('object_type_id','object_type')
    """)
    op.execute("""
        CREATE TRIGGER trg_sys_role_scope_scope_immutable BEFORE UPDATE ON sys_role_scope
        FOR EACH ROW EXECUTE FUNCTION ontology_reference_guard('id', 'sys_role_scope')
    """)
    op.execute("ALTER TABLE sys_role_scope ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sys_role_scope FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY ontology_scope ON sys_role_scope
        USING (ontology_id={GUC}) WITH CHECK (ontology_id={GUC})
    """)
    for table in ("service_account", "api_key_usage"):
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {table} TO rengu_ontology_scoped")
        op.execute(f"GRANT USAGE,SELECT ON SEQUENCE {table}_id_seq TO rengu_ontology_scoped")
    for table, column, comment in COLUMN_COMMENTS:
        op.execute(f"COMMENT ON COLUMN {table}.{column} IS '{comment}'")


def downgrade():
    raise RuntimeError(
        "Preserve service accounts, key policies and per-ontology row scopes; "
        "destructive downgrade is blocked"
    )
