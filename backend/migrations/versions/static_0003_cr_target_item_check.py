"""变更单拆表：cr_target / cr_item / cr_check / cr_impact / cr_trial（static-api.md §1.2、§1.3）。

变更单写接口（发起 / 加入 / 保存草稿 / 校验 / 改动）用；目标通用 kind + 业务编码，不做跨模块外键。
down_revision = qa_0003（static-r3 时 master 的 head；合并时如有新 head，PDE 改这一行）。

Revision ID: static_0003
Revises: qa_0003
Create Date: 2026-10-26 08:25:51.742493+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "static_0003"
down_revision: str | None = "qa_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cr_check",
        sa.Column("cr_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=8), nullable=False, comment="V1..V17"),
        sa.Column("target_db", sa.String(length=8), nullable=False, comment="'' | seed（种子基线）| dm8 | kes"),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False, comment="block|warn|info|pass|na"),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["cr_id"],
            ["change_request.id"],
            name=op.f("fk_cr_check_cr_id_change_request"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cr_check")),
        sa.UniqueConstraint("cr_id", "code", "target_db", name=op.f("uq_cr_check_cr_id_code_target_db")),
        comment="变更单校验结果",
    )
    op.create_index(op.f("ix_cr_check_cr_id"), "cr_check", ["cr_id"], unique=False)
    op.create_table(
        "cr_impact",
        sa.Column("cr_id", sa.BigInteger(), nullable=False),
        sa.Column("grp", sa.String(length=8), nullable=False, comment="direct|defs|usage"),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("cnt", sa.Integer(), nullable=True),
        sa.Column("ref_kind", sa.String(length=16), nullable=True),
        sa.Column("ref_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["cr_id"],
            ["change_request.id"],
            name=op.f("fk_cr_impact_cr_id_change_request"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cr_impact")),
        comment="变更单影响面",
    )
    op.create_index(op.f("ix_cr_impact_cr_id"), "cr_impact", ["cr_id"], unique=False)
    op.create_table(
        "cr_item",
        sa.Column("cr_id", sa.BigInteger(), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("section", sa.String(length=32), nullable=False, comment="properties|links|params|conds…"),
        sa.Column("item_key", sa.String(length=128), nullable=False, comment="行键（属性名、取值、条件号…）"),
        sa.Column("op", sa.String(length=8), nullable=False, comment="add|update|delete"),
        sa.Column(
            "before",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "after",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("type_label", sa.String(length=32), nullable=True, comment="改动列表「类型」列"),
        sa.Column("field_label", sa.String(length=128), nullable=True, comment="改动列表「字段」列"),
        sa.Column(
            "before_text", sa.Text(), nullable=True, comment="改动列表显示文字（可空，空时由 before 生成）"
        ),
        sa.Column("after_text", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True, comment="保存人姓名"),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["cr_id"], ["change_request.id"], name=op.f("fk_cr_item_cr_id_change_request"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cr_item")),
        sa.UniqueConstraint(
            "cr_id",
            "target_kind",
            "target_id",
            "section",
            "item_key",
            name=op.f("uq_cr_item_cr_id_target_kind_target_id_section_item_key"),
        ),
        comment="变更单改动行",
    )
    op.create_index(op.f("ix_cr_item_cr_id"), "cr_item", ["cr_id"], unique=False)
    op.create_table(
        "cr_target",
        sa.Column("cr_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "target_kind",
            sa.String(length=16),
            nullable=False,
            comment="object|link|metric|dimension|knowledge|action|event|rule",
        ),
        sa.Column("target_id", sa.String(length=64), nullable=False, comment="定义业务编码（前端 id）"),
        sa.Column(
            "target_name", sa.String(length=128), nullable=True, comment="显示名（改动列表「对象」列）"
        ),
        sa.Column("is_new", sa.Boolean(), nullable=False, comment="本单新建的定义"),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["cr_id"],
            ["change_request.id"],
            name=op.f("fk_cr_target_cr_id_change_request"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cr_target")),
        sa.UniqueConstraint(
            "cr_id", "target_kind", "target_id", name=op.f("uq_cr_target_cr_id_target_kind_target_id")
        ),
        comment="变更单涉及的定义",
    )
    op.create_index(op.f("ix_cr_target_cr_id"), "cr_target", ["cr_id"], unique=False)
    op.create_index("ix_cr_target_kind_id", "cr_target", ["target_kind", "target_id"], unique=False)
    op.create_table(
        "cr_trial",
        sa.Column("cr_id", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(length=32), nullable=False),
        sa.Column("before_val", sa.String(length=64), nullable=True),
        sa.Column("after_val", sa.String(length=64), nullable=True),
        sa.Column("delta", sa.String(length=32), nullable=True),
        sa.Column("scope", sa.String(length=128), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["cr_id"],
            ["change_request.id"],
            name=op.f("fk_cr_trial_cr_id_change_request"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cr_trial")),
        sa.UniqueConstraint("cr_id", "period", name=op.f("uq_cr_trial_cr_id_period")),
        comment="变更单试算",
    )
    op.create_index(op.f("ix_cr_trial_cr_id"), "cr_trial", ["cr_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cr_trial_cr_id"), table_name="cr_trial")
    op.drop_table("cr_trial")
    op.drop_index("ix_cr_target_kind_id", table_name="cr_target")
    op.drop_index(op.f("ix_cr_target_cr_id"), table_name="cr_target")
    op.drop_table("cr_target")
    op.drop_index(op.f("ix_cr_item_cr_id"), table_name="cr_item")
    op.drop_table("cr_item")
    op.drop_index(op.f("ix_cr_impact_cr_id"), table_name="cr_impact")
    op.drop_table("cr_impact")
    op.drop_index(op.f("ix_cr_check_cr_id"), table_name="cr_check")
    op.drop_table("cr_check")
