"""动态本体：决策评估记录 decision_log（POST /decisions:evaluate）。

- 只追加：append_only 触发器（app/db/ddl.py，禁止 UPDATE / DELETE / TRUNCATE）；
- 结构同 dynamic_0024 的领域表：ontology_id 默认取会话空间、唯一键带 ontology_id、
  FORCE RLS + ontology_scope 策略、授权给 rengu_ontology_scoped；
- 不写任何数据。降级删表。

Revision ID: dynamic_0027
Revises: admin_0026
Create Date: 2026-10-27 17:00:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from app.db.ddl import disable_append_only, enable_append_only

revision: str = "dynamic_0027"
down_revision: str | None = "admin_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
DOMAIN_TABLES = ["decision_log"]

_SCOPE = "NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"


def upgrade() -> None:
    op.create_table(
        "decision_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ontology_id",
            sa.BigInteger(),
            sa.ForeignKey("ontology_workspace.id"),
            nullable=False,
            server_default=sa.text(_SCOPE),
        ),
        sa.Column("code", sa.String(64), nullable=False, comment="业务编号（前端 id / decisionId）"),
        sa.Column("caller_kind", sa.String(16), nullable=False, comment="user / service_account / ai"),
        sa.Column("caller_id", sa.String(64), nullable=False),
        sa.Column("caller_name", sa.String(200)),
        sa.Column("api_key_prefix", sa.String(32)),
        sa.Column("ai_session_id", sa.String(64)),
        sa.Column("ai_round_id", sa.String(64)),
        sa.Column("obj_kind", sa.String(32), nullable=False),
        sa.Column("obj_id", sa.String(64), nullable=False),
        sa.Column("alert_code", sa.String(64)),
        sa.Column("input", JSONB(), nullable=False, comment="请求 + 评估时的告警 / 命中 / 生命周期上下文"),
        sa.Column("output", JSONB(), nullable=False, comment="排序后的建议动作（含试运行与策略）"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("ontology_id", "code", name="uq_decision_log_ontology_id_code"),
        comment="决策评估记录（只追加）",
    )
    op.create_index("ix_decision_log_ontology_id", "decision_log", ["ontology_id"])
    op.create_index("ix_decision_log_obj_kind_obj_id", "decision_log", ["obj_kind", "obj_id"])
    op.create_index("ix_decision_log_ai_session_id", "decision_log", ["ai_session_id"])
    op.create_index("ix_decision_log_alert_code", "decision_log", ["alert_code"])
    for sql in enable_append_only("decision_log"):
        op.execute(sql)
    op.execute("ALTER TABLE decision_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE decision_log FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY ontology_scope ON decision_log
        USING (ontology_id={_SCOPE}) WITH CHECK (ontology_id={_SCOPE})
    """)
    op.execute("GRANT SELECT,INSERT ON decision_log TO rengu_ontology_scoped")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE decision_log_id_seq TO rengu_ontology_scoped")


def downgrade() -> None:
    for sql in disable_append_only("decision_log"):
        op.execute(sql)
    op.drop_table("decision_log")
