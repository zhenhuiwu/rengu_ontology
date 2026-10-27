"""动态本体：情景推演模板改为按本体配置的 scenario_template 表。

- 结构与 ontology_0018 的领域表一致：ontology_id 默认取会话空间、唯一键带 ontology_id、
  空间不可改触发器、FORCE RLS + ontology_scope 策略、授权给 rengu_ontology_scoped；
- 迁移原有配置（不是新造数据）：此前 ontology 1 的模板来自 seeds/json/dynamic/SC_TEMPLATES.json，
  原样写入 ontology 1（空间不存在则跳过）；其他空间不写，接口返回空列表、前端显示空状态；
- 推演执行器仍未配置，:run 维持 503，本迁移不改变。
降级删表：模板定义仍保留在 SC_TEMPLATES.json，可以重新导入。

Revision ID: dynamic_0024
Revises: dynamic_0023
Create Date: 2026-10-27 16:30:00+08:00
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "dynamic_0024"
down_revision: str | None = "dynamic_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
DOMAIN_TABLES = ["scenario_template"]

_JSON = Path(__file__).resolve().parents[2] / "seeds" / "json" / "dynamic" / "SC_TEMPLATES.json"
_SCOPE = "NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"


def upgrade() -> None:
    op.create_table(
        "scenario_template",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ontology_id",
            sa.BigInteger(),
            sa.ForeignKey("ontology_workspace.id"),
            nullable=False,
            server_default=sa.text(_SCOPE),
        ),
        sa.Column("code", sa.String(64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("engine", sa.String(64), nullable=False, comment="执行器键（ENGINES）"),
        sa.Column("object_type", sa.String(64), nullable=False, comment="目标对象类型 api_name"),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("definition", JSONB(), nullable=False, comment="模板定义（SC_TEMPLATES 单条）"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_seed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("ontology_id", "code"),
    )
    op.create_index("ix_scenario_template_ontology_id", "scenario_template", ["ontology_id"])
    op.execute("""
        CREATE TRIGGER trg_scenario_template_scope_immutable BEFORE UPDATE ON scenario_template
        FOR EACH ROW EXECUTE FUNCTION ontology_reference_guard('id', 'scenario_template')
    """)
    op.execute("ALTER TABLE scenario_template ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE scenario_template FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY ontology_scope ON scenario_template
        USING (ontology_id={_SCOPE}) WITH CHECK (ontology_id={_SCOPE})
    """)
    op.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON scenario_template TO rengu_ontology_scoped")
    op.execute("GRANT USAGE,SELECT ON SEQUENCE scenario_template_id_seq TO rengu_ontology_scoped")

    bind = op.get_bind()
    if not bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM ontology_workspace WHERE id = 1)")):
        return
    # 只在本事务内切到空间 1，RLS WITH CHECK 对非超级用户属主同样成立。
    bind.execute(sa.text("SELECT set_config('rengu.ontology_id', '1', true)"))
    rows = json.loads(_JSON.read_text(encoding="utf-8"))
    for i, t in enumerate(rows):
        bind.execute(
            sa.text(
                "INSERT INTO scenario_template (ontology_id, code, name, engine, object_type, sort,"
                " definition, is_seed, created_by) VALUES (1, :code, :name, :engine, :ot, :sort,"
                " CAST(:d AS jsonb), true, 'migration') ON CONFLICT (ontology_id, code) DO NOTHING"
            ),
            {
                "code": t["code"],
                "name": t["name"],
                "engine": t["engine"],
                "ot": t["objectType"],
                "sort": i,
                "d": json.dumps(t, ensure_ascii=False),
            },
        )
    bind.execute(sa.text("SELECT set_config('rengu.ontology_id', '', true)"))


def downgrade() -> None:
    op.drop_table("scenario_template")
