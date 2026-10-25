"""动态本体第二部分：规则 rule / rule_version / rule_draft / rule_hit、情景推演 scenario / scenario_result /
scenario_impact、生命周期 lifecycle / lifecycle_version / lifecycle_state / lifecycle_transition /
lifecycle_draft / lifecycle_change。

真外键：action_type、event_type、object_type、ontology_release、alert（都已在 master）。
草稿表 rule_draft / lifecycle_draft 的 cr_code 是变更单软引用（静态变更单表合并后再补 FK）。
rule_version / rule_hit / lifecycle_version / lifecycle_change 只追加（数据库触发器兜底）。

Revision ID: dynamic_0002
Revises: event_0002
Create Date: 2026-10-26 00:19:55.923231+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.ddl import disable_append_only, enable_append_only

revision: str = "dynamic_0002"
down_revision: str | None = "event_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = ("rule_version", "rule_hit", "lifecycle_version", "lifecycle_change")


def upgrade() -> None:
    op.create_table(
        "lifecycle",
        sa.Column("object_type_id", sa.BigInteger(), nullable=False),
        sa.Column("object_name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True, comment="当前生效版本；null = 回滚后不在快照里"),
        sa.Column("field", sa.String(length=128), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("updated_on", sa.DateTime(timezone=True), nullable=False, comment="mock updatedAt"),
        sa.Column(
            "rule_code",
            sa.String(length=32),
            nullable=True,
            comment="状态机校验规则编号（SM-ORD，非 rule 表）",
        ),
        sa.Column("desc", sa.Text(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["object_type_id"], ["object_type.id"], name=op.f("fk_lifecycle_object_type_id_object_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle")),
        sa.UniqueConstraint("code", name=op.f("uq_lifecycle_code")),
        sa.UniqueConstraint("object_type_id", name=op.f("uq_lifecycle_object_type_id")),
        comment="对象类型生命周期（当前版本指针）",
    )
    op.create_table(
        "lifecycle_draft",
        sa.Column("lifecycle_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "cr_code", sa.String(length=32), nullable=False, comment="变更单号（软引用，FK to add later）"
        ),
        sa.Column(
            "edits",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="{addStates,addTransitions,editTransitions,removeTransitions}",
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["lifecycle_id"], ["lifecycle.id"], name=op.f("fk_lifecycle_draft_lifecycle_id_lifecycle")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_draft")),
        sa.UniqueConstraint("lifecycle_id", "cr_code", name=op.f("uq_lifecycle_draft_lifecycle_id_cr_code")),
        comment="生命周期草稿改动（LcDraft，暂代 cr_item）",
    )
    op.create_table(
        "lifecycle_version",
        sa.Column("lifecycle_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("by", sa.String(length=64), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column(
            "undo",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="{states, transitions}",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=True, comment="本体版本（ontology_release.id）"),
        sa.ForeignKeyConstraint(
            ["lifecycle_id"], ["lifecycle.id"], name=op.f("fk_lifecycle_version_lifecycle_id_lifecycle")
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["ontology_release.id"],
            name=op.f("fk_lifecycle_version_release_id_ontology_release"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_version")),
        sa.UniqueConstraint("code", name=op.f("uq_lifecycle_version_code")),
        sa.UniqueConstraint(
            "lifecycle_id", "version", name=op.f("uq_lifecycle_version_lifecycle_id_version")
        ),
        comment="生命周期版本（只追加）",
    )
    op.create_index(
        op.f("ix_lifecycle_version_lifecycle_id"), "lifecycle_version", ["lifecycle_id"], unique=False
    )
    op.create_index(
        op.f("ix_lifecycle_version_release_id"), "lifecycle_version", ["release_id"], unique=False
    )
    op.create_table(
        "scenario",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("created_on", sa.DateTime(timezone=True), nullable=False, comment="mock createdAt"),
        sa.Column("base_release_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "running",
                "done",
                "failed",
                name="scenariostatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "drivers",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column(
            "staged",
            sa.JSON().with_variant(postgresql.ARRAY(sa.String(length=64)), "postgresql"),
            nullable=False,
            comment="已转为暂存动作的动作编号（action_run.code）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["base_release_id"],
            ["ontology_release.id"],
            name=op.f("fk_scenario_base_release_id_ontology_release"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario")),
        sa.UniqueConstraint("code", name=op.f("uq_scenario_code")),
        comment="情景推演方案",
    )
    op.create_index(op.f("ix_scenario_status"), "scenario", ["status"], unique=False)
    op.create_table(
        "lifecycle_state",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "initial",
                "middle",
                "final",
                name="lcstatekind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("desc", sa.Text(), nullable=False),
        sa.Column("sla", sa.String(length=200), nullable=True),
        sa.Column("count", sa.BigInteger(), nullable=False, comment="当前处于该状态的对象数"),
        sa.Column("x", sa.Integer(), nullable=False),
        sa.Column("y", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["lifecycle_version.id"],
            name=op.f("fk_lifecycle_state_version_id_lifecycle_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_state")),
        sa.UniqueConstraint("code", name=op.f("uq_lifecycle_state_code")),
        sa.UniqueConstraint("version_id", "key", name=op.f("uq_lifecycle_state_version_id_key")),
        comment="生命周期状态",
    )
    op.create_index(op.f("ix_lifecycle_state_version_id"), "lifecycle_state", ["version_id"], unique=False)
    op.create_table(
        "rule",
        sa.Column("version", sa.Integer(), nullable=True, comment="当前生效版本；null = 回滚后不在快照里"),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("desc", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "changing",
                "disabled",
                name="rulestatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("core", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("obj_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                "high",
                "medium",
                "low",
                name="alertlevel",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "output",
            sa.Enum(
                "alert", "notify", name="ruleoutput", native_enum=False, create_constraint=True, length=16
            ),
            nullable=False,
        ),
        sa.Column(
            "conds",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="[{metric,metricVer,field,op,value}]",
        ),
        sa.Column("logic", sa.String(length=8), nullable=False),
        sa.Column("expr", sa.Text(), nullable=False),
        sa.Column("dedupe", sa.String(length=200), nullable=False),
        sa.Column("cooldown", sa.String(length=64), nullable=False),
        sa.Column(
            "trigger",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="{kind,cron,text,eventId,filter}",
        ),
        sa.Column("action_type_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "auto_stage",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="{enabled,actionTypeId,confirmer,mapping[{param,from}]}",
        ),
        sa.Column("weekly", sa.Integer(), nullable=False, comment="周命中数（mock weekly）"),
        sa.Column("confirmer", sa.String(length=64), nullable=True, comment="固定确认人（RULE_CONFIRMER）"),
        sa.Column("published_by", sa.String(length=64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=True, comment="本体版本（ontology_release.id）"),
        sa.ForeignKeyConstraint(
            ["action_type_id"], ["action_type.id"], name=op.f("fk_rule_action_type_id_action_type")
        ),
        sa.ForeignKeyConstraint(
            ["release_id"], ["ontology_release.id"], name=op.f("fk_rule_release_id_ontology_release")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule")),
        sa.UniqueConstraint("code", name=op.f("uq_rule_code")),
        comment="规则（当前定义；RULES 5 条核心 + RULE_DEFS 16 条合并）",
    )
    op.create_index(op.f("ix_rule_action_type_id"), "rule", ["action_type_id"], unique=False)
    op.create_index(op.f("ix_rule_release_id"), "rule", ["release_id"], unique=False)
    op.create_index(op.f("ix_rule_status"), "rule", ["status"], unique=False)
    op.create_table(
        "scenario_impact",
        sa.Column("scenario_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, comment="结果里的顺序（按影响绝对值降序）"),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("store_id", sa.String(length=64), nullable=False),
        sa.Column("store_name", sa.String(length=200), nullable=False),
        sa.Column("base_sales", sa.BigInteger(), nullable=False),
        sa.Column("sales", sa.BigInteger(), nullable=False),
        sa.Column("stockout_base", sa.Double(), nullable=False),
        sa.Column("stockout", sa.Double(), nullable=False),
        sa.Column("suggest", sa.String(length=64), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenario.id"], name=op.f("fk_scenario_impact_scenario_id_scenario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario_impact")),
        sa.UniqueConstraint(
            "scenario_id", "sku", "store_id", name=op.f("uq_scenario_impact_scenario_id_sku_store_id")
        ),
        comment="推演影响明细（SKU × 门店）",
    )
    op.create_index(op.f("ix_scenario_impact_scenario_id"), "scenario_impact", ["scenario_id"], unique=False)
    op.create_table(
        "scenario_result",
        sa.Column("scenario_id", sa.BigInteger(), nullable=False),
        sa.Column("ran_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "scope",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "drivers",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "weeks",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "base_sales",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "sales",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "base_margin",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "margin",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "kpis",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenario.id"], name=op.f("fk_scenario_result_scenario_id_scenario")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenario_result")),
        sa.UniqueConstraint("scenario_id", name=op.f("uq_scenario_result_scenario_id")),
        comment="推演结果（每个方案最近一次）",
    )
    op.create_table(
        "lifecycle_transition",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("tr_code", sa.String(length=16), nullable=False),
        sa.Column("from_state_id", sa.BigInteger(), nullable=False),
        sa.Column("to_state_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum(
                "event",
                "action",
                "system",
                "manual",
                name="lctrigger",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("by", sa.String(length=200), nullable=False),
        sa.Column("event_type_id", sa.BigInteger(), nullable=True),
        sa.Column("action_type_id", sa.BigInteger(), nullable=True),
        sa.Column("guard", sa.Text(), nullable=False),
        sa.Column("effect", sa.Text(), nullable=False),
        sa.Column("weekly", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_lifecycle_transition_action_type_id_action_type"),
        ),
        sa.ForeignKeyConstraint(
            ["event_type_id"],
            ["event_type.id"],
            name=op.f("fk_lifecycle_transition_event_type_id_event_type"),
        ),
        sa.ForeignKeyConstraint(
            ["from_state_id"],
            ["lifecycle_state.id"],
            name=op.f("fk_lifecycle_transition_from_state_id_lifecycle_state"),
        ),
        sa.ForeignKeyConstraint(
            ["to_state_id"],
            ["lifecycle_state.id"],
            name=op.f("fk_lifecycle_transition_to_state_id_lifecycle_state"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["lifecycle_version.id"],
            name=op.f("fk_lifecycle_transition_version_id_lifecycle_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_transition")),
        sa.UniqueConstraint("code", name=op.f("uq_lifecycle_transition_code")),
        sa.UniqueConstraint("version_id", "tr_code", name=op.f("uq_lifecycle_transition_version_id_tr_code")),
        comment="生命周期迁移",
    )
    op.create_index(
        op.f("ix_lifecycle_transition_version_id"), "lifecycle_transition", ["version_id"], unique=False
    )
    op.create_table(
        "rule_draft",
        sa.Column("rule_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "cr_code", sa.String(length=32), nullable=False, comment="变更单号（软引用，FK to add later）"
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "patch",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="RULE_DRAFTS 的改动（Partial<RuleDef>）",
        ),
        sa.Column("by", sa.String(length=64), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change", sa.Text(), nullable=False, comment="版本历史里草稿行的说明"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["rule.id"], name=op.f("fk_rule_draft_rule_id_rule")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_draft")),
        sa.UniqueConstraint("rule_id", "cr_code", name=op.f("uq_rule_draft_rule_id_cr_code")),
        comment="规则草稿改动（暂代 cr_item）",
    )
    op.create_index(op.f("ix_rule_draft_rule_id"), "rule_draft", ["rule_id"], unique=False)
    op.create_table(
        "rule_hit",
        sa.Column("rule_id", sa.BigInteger(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=True),
        sa.Column(
            "seq", sa.Integer(), nullable=False, comment="同一规则内 hitsOf 顺序（at 倒序，同秒按 mock 顺序）"
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("obj_kind", sa.String(length=32), nullable=False),
        sa.Column("obj_id", sa.String(length=64), nullable=False),
        sa.Column("obj_label", sa.String(length=200), nullable=False),
        sa.Column("store_id", sa.String(length=64), nullable=True),
        sa.Column("store_label", sa.String(length=200), nullable=True),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column("threshold", sa.String(length=200), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column(
            "result",
            sa.Enum(
                "alert",
                "notified",
                "deduped",
                name="hitresult",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("alert_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alert.id"], name=op.f("fk_rule_hit_alert_id_alert")),
        sa.ForeignKeyConstraint(["rule_id"], ["rule.id"], name=op.f("fk_rule_hit_rule_id_rule")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_hit")),
        sa.UniqueConstraint("code", name=op.f("uq_rule_hit_code")),
        comment="规则命中记录（只追加）",
    )
    op.create_index(op.f("ix_rule_hit_alert_id"), "rule_hit", ["alert_id"], unique=False)
    op.create_index(op.f("ix_rule_hit_at"), "rule_hit", ["at"], unique=False)
    op.create_index(op.f("ix_rule_hit_rule_id"), "rule_hit", ["rule_id"], unique=False)
    op.create_table(
        "rule_version",
        sa.Column("rule_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("by", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["rule.id"], name=op.f("fk_rule_version_rule_id_rule")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_version")),
        sa.UniqueConstraint("rule_id", "version", name=op.f("uq_rule_version_rule_id_version")),
        comment="规则版本历史（只追加）",
    )
    op.create_index(op.f("ix_rule_version_rule_id"), "rule_version", ["rule_id"], unique=False)
    op.create_table(
        "lifecycle_change",
        sa.Column("lifecycle_id", sa.BigInteger(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("obj_id", sa.String(length=64), nullable=False),
        sa.Column("obj_label", sa.String(length=200), nullable=False),
        sa.Column("from_state", sa.String(length=64), nullable=False),
        sa.Column("to_state", sa.String(length=64), nullable=False),
        sa.Column("transition_id", sa.BigInteger(), nullable=True, comment="命中的迁移；null = 非法迁移"),
        sa.Column("by", sa.String(length=200), nullable=False),
        sa.Column("alert_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alert.id"], name=op.f("fk_lifecycle_change_alert_id_alert")),
        sa.ForeignKeyConstraint(
            ["lifecycle_id"], ["lifecycle.id"], name=op.f("fk_lifecycle_change_lifecycle_id_lifecycle")
        ),
        sa.ForeignKeyConstraint(
            ["transition_id"],
            ["lifecycle_transition.id"],
            name=op.f("fk_lifecycle_change_transition_id_lifecycle_transition"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_change")),
        sa.UniqueConstraint("code", name=op.f("uq_lifecycle_change_code")),
        comment="对象状态变化记录（数据同步逐条；只追加）",
    )
    op.create_index(op.f("ix_lifecycle_change_at"), "lifecycle_change", ["at"], unique=False)
    op.create_index(
        op.f("ix_lifecycle_change_lifecycle_id"), "lifecycle_change", ["lifecycle_id"], unique=False
    )
    for table in APPEND_ONLY:
        for sql in enable_append_only(table):
            op.execute(sql)


def downgrade() -> None:
    for table in APPEND_ONLY:
        for sql in disable_append_only(table):
            op.execute(sql)
    op.drop_index(op.f("ix_lifecycle_change_lifecycle_id"), table_name="lifecycle_change")
    op.drop_index(op.f("ix_lifecycle_change_at"), table_name="lifecycle_change")
    op.drop_table("lifecycle_change")
    op.drop_index(op.f("ix_rule_version_rule_id"), table_name="rule_version")
    op.drop_table("rule_version")
    op.drop_index(op.f("ix_rule_hit_rule_id"), table_name="rule_hit")
    op.drop_index(op.f("ix_rule_hit_at"), table_name="rule_hit")
    op.drop_index(op.f("ix_rule_hit_alert_id"), table_name="rule_hit")
    op.drop_table("rule_hit")
    op.drop_index(op.f("ix_rule_draft_rule_id"), table_name="rule_draft")
    op.drop_table("rule_draft")
    op.drop_index(op.f("ix_lifecycle_transition_version_id"), table_name="lifecycle_transition")
    op.drop_table("lifecycle_transition")
    op.drop_table("scenario_result")
    op.drop_index(op.f("ix_scenario_impact_scenario_id"), table_name="scenario_impact")
    op.drop_table("scenario_impact")
    op.drop_index(op.f("ix_rule_status"), table_name="rule")
    op.drop_index(op.f("ix_rule_release_id"), table_name="rule")
    op.drop_index(op.f("ix_rule_action_type_id"), table_name="rule")
    op.drop_table("rule")
    op.drop_index(op.f("ix_lifecycle_state_version_id"), table_name="lifecycle_state")
    op.drop_table("lifecycle_state")
    op.drop_index(op.f("ix_scenario_status"), table_name="scenario")
    op.drop_table("scenario")
    op.drop_index(op.f("ix_lifecycle_version_release_id"), table_name="lifecycle_version")
    op.drop_index(op.f("ix_lifecycle_version_lifecycle_id"), table_name="lifecycle_version")
    op.drop_table("lifecycle_version")
    op.drop_table("lifecycle_draft")
    op.drop_table("lifecycle")
