"""action：动作类型定义表（action_type 及参数、提交条件、本体编辑、副作用、规则引用、版本表）

外键只指向本链上更早的 ext_system；retired_release_id 对 ontology_release
为软引用（版本表允许 TRUNCATE 检查，见 test_versioning）；规则引用 action_type_rule.rule_code
暂为软引用，动态 rule 合并后另写迁移补外键。action_type_version 只追加（触发器兜底）。

Revision ID: action_0001
Revises: integration_0001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.ddl import disable_append_only, enable_append_only

revision: str = "action_0001"
down_revision: str | None = "integration_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_type",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("api", sa.String(length=64), nullable=False, comment="API 名（唯一）"),
        sa.Column("grp", sa.String(length=32), nullable=False, comment="分组（接口 group）"),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "changing",
                "draft",
                name="typestatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("version", sa.String(length=16), nullable=False, comment="当前版本 vN"),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("target_api", sa.String(length=64), nullable=False),
        sa.Column(
            "target_kind",
            sa.String(length=32),
            nullable=True,
            comment="对象类型前端 id（软引用，静态合并后改外键）",
        ),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("desc_tpl", sa.Text(), nullable=False, comment="说明模板（{sys} 占位）"),
        sa.Column("runners", sa.Text(), nullable=False),
        sa.Column("approval", sa.Text(), nullable=False),
        sa.Column(
            "sys_id", sa.BigInteger(), nullable=True, comment="写回外部系统（ext_system.id），空 = 本体内部"
        ),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("biz_key_template", sa.String(length=255), nullable=False, comment="接口 idemKey"),
        sa.Column(
            "retired_release_id",
            sa.BigInteger(),
            nullable=True,
            comment="整版回滚下线于哪一版（ontology_release.id，软引用）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["sys_id"], ["ext_system.id"], name=op.f("fk_action_type_sys_id_ext_system")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_type")),
        sa.UniqueConstraint("api", name=op.f("uq_action_type_api")),
        sa.UniqueConstraint("code", name=op.f("uq_action_type_code")),
        comment="动作类型（当前版本）",
    )
    op.create_index(op.f("ix_action_type_sys_id"), "action_type", ["sys_id"], unique=False)
    op.create_table(
        "action_param",
        sa.Column("action_type_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("api", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("default_text", sa.Text(), nullable=False, comment="接口 def"),
        sa.Column("rule_text", sa.Text(), nullable=False, comment="接口 rule"),
        sa.Column(
            "rule_expr",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_action_param_action_type_id_action_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_param")),
        sa.UniqueConstraint("action_type_id", "seq", name=op.f("uq_action_param_action_type_id_seq")),
        comment="动作参数",
    )
    op.create_index(
        "uq_action_param_action_type_id_api_nonseed",
        "action_param",
        ["action_type_id", "api"],
        unique=True,
        postgresql_where=sa.text("NOT is_seed"),
    )
    op.create_table(
        "action_type_rule",
        sa.Column("action_type_id", sa.BigInteger(), nullable=False),
        sa.Column("rule_code", sa.String(length=32), nullable=False, comment="规则编码 R-…（软引用）"),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("mode_text", sa.Text(), nullable=True, comment="使用方式原文"),
        sa.Column(
            "generates",
            sa.Boolean(),
            nullable=False,
            comment="规则命中生成本动作（RULE_ACTION；动态合并后改读 rule）",
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_action_type_rule_action_type_id_action_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_type_rule")),
        sa.UniqueConstraint(
            "action_type_id", "rule_code", name=op.f("uq_action_type_rule_action_type_id_rule_code")
        ),
        comment="动作类型 ↔ 规则",
    )
    op.create_table(
        "action_type_version",
        sa.Column("action_type_id", sa.BigInteger(), nullable=False),
        sa.Column("ver", sa.String(length=16), nullable=False),
        sa.Column("release_ver", sa.String(length=32), nullable=False, comment="本体发布版本号（软引用）"),
        sa.Column("change_note", sa.Text(), nullable=False, comment="接口 change"),
        sa.Column("by_name", sa.String(length=64), nullable=False, comment="接口 by（姓名原文）"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "definition",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="该版定义（seed 没有，留空）",
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_action_type_version_action_type_id_action_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_type_version")),
        sa.UniqueConstraint("action_type_id", "ver", name=op.f("uq_action_type_version_action_type_id_ver")),
        comment="动作类型版本（只追加）",
    )
    op.create_table(
        "ontology_edit_rule",
        sa.Column("action_type_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "op",
            sa.Enum(
                "modify",
                "create",
                "link",
                name="editop",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("obj", sa.String(length=128), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_ontology_edit_rule_action_type_id_action_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ontology_edit_rule")),
        sa.UniqueConstraint("action_type_id", "seq", name=op.f("uq_ontology_edit_rule_action_type_id_seq")),
        comment="本体编辑",
    )
    op.create_table(
        "side_effect",
        sa.Column("action_type_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "notify",
                "event",
                "webhook",
                name="effectkind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("when_text", sa.Text(), nullable=False, comment="接口 when"),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_side_effect_action_type_id_action_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_side_effect")),
        sa.UniqueConstraint("action_type_id", "target", name=op.f("uq_side_effect_action_type_id_target")),
        comment="副作用",
    )
    op.create_table(
        "submission_criterion",
        sa.Column("action_type_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "current_user",
                "parameter",
                "context_object",
                name="criterionkind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("expr_text", sa.Text(), nullable=False, comment="接口 expr"),
        sa.Column(
            "expr_ast",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("fail_msg", sa.Text(), nullable=False, comment="接口 fail"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_type_id"],
            ["action_type.id"],
            name=op.f("fk_submission_criterion_action_type_id_action_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_criterion")),
        sa.UniqueConstraint(
            "action_type_id", "name", name=op.f("uq_submission_criterion_action_type_id_name")
        ),
        comment="提交条件",
    )

    for sql in enable_append_only("action_type_version"):
        op.execute(sql)


def downgrade() -> None:
    for sql in disable_append_only("action_type_version"):
        op.execute(sql)
    op.drop_table("submission_criterion")
    op.drop_table("side_effect")
    op.drop_table("ontology_edit_rule")
    op.drop_table("action_type_version")
    op.drop_table("action_type_rule")
    op.drop_index(
        "uq_action_param_action_type_id_api_nonseed",
        table_name="action_param",
        postgresql_where=sa.text("NOT is_seed"),
    )
    op.drop_table("action_param")
    op.drop_index(op.f("ix_action_type_sys_id"), table_name="action_type")
    op.drop_table("action_type")
