"""event：事件类型定义表（event_type、载荷字段、信封字段、订阅方、版本表）

规则引用 event_type.rule_code、event_subscription.rule_code 暂为软引用（动态 rule 合并后补外键）。
event_type_version 只追加（触发器兜底）。

Revision ID: event_0001
Revises: action_0001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.ddl import disable_append_only, enable_append_only

revision: str = "event_0001"
down_revision: str | None = "action_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_envelope_field",
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("api", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sample", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_envelope_field")),
        sa.UniqueConstraint("api", name=op.f("uq_event_envelope_field_api")),
        comment="事件信封字段（全局）",
    )
    op.create_table(
        "event_type",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("api", sa.String(length=64), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "sync",
                "rule",
                "action",
                "external",
                name="eventsource",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column(
            "rule_code", sa.String(length=32), nullable=True, comment="来源规则 R-…（软引用，接口 ruleId）"
        ),
        sa.Column("object", sa.String(length=64), nullable=False),
        sa.Column("object_api", sa.String(length=64), nullable=False),
        sa.Column("obj_kind", sa.String(length=32), nullable=True, comment="对象类型前端 id（软引用）"),
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
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, comment="接口 desc"),
        sa.Column("dedup_key_rule", sa.Text(), nullable=False, comment="接口 dedupKey"),
        sa.Column("dedup_window", sa.String(length=64), nullable=False, comment="原文"),
        sa.Column("delivery", sa.String(length=32), nullable=False),
        sa.Column("ordering", sa.String(length=64), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False, comment="接口 retention = 'N 天'"),
        sa.Column("weekly", sa.Integer(), nullable=False, comment="近 7 天事件数 [推断]"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_type")),
        sa.UniqueConstraint("api", name=op.f("uq_event_type_api")),
        sa.UniqueConstraint("code", name=op.f("uq_event_type_code")),
        comment="事件类型（当前版本）",
    )
    op.create_table(
        "event_payload_field",
        sa.Column("event_type_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("api", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, comment="接口 desc"),
        sa.Column("sample", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["event_type_id"],
            ["event_type.id"],
            name=op.f("fk_event_payload_field_event_type_id_event_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_payload_field")),
        sa.UniqueConstraint("event_type_id", "api", name=op.f("uq_event_payload_field_event_type_id_api")),
        comment="事件载荷字段",
    )
    op.create_table(
        "event_subscription",
        sa.Column("event_type_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "rule",
                "automation",
                "notify",
                "external",
                name="subscriberkind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "rule_code", sa.String(length=32), nullable=True, comment="规则 R-…（软引用，接口 ruleId）"
        ),
        sa.Column("filter_text", sa.Text(), nullable=False, comment="接口 filter"),
        sa.Column(
            "filter_expr",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("then_text", sa.Text(), nullable=False, comment="接口 then"),
        sa.Column(
            "status",
            sa.Enum(
                "enabled",
                "disabled",
                "error",
                name="subscriberstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("weekly", sa.Integer(), nullable=False, comment="近 7 天投递数 [推断]"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["event_type_id"],
            ["event_type.id"],
            name=op.f("fk_event_subscription_event_type_id_event_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_subscription")),
        sa.UniqueConstraint("event_type_id", "name", name=op.f("uq_event_subscription_event_type_id_name")),
        comment="事件订阅方",
    )
    op.create_table(
        "event_type_version",
        sa.Column("event_type_id", sa.BigInteger(), nullable=False),
        sa.Column("ver", sa.String(length=16), nullable=False),
        sa.Column("release_ver", sa.String(length=32), nullable=False, comment="本体发布版本号（软引用）"),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("by_name", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "definition",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["event_type_id"], ["event_type.id"], name=op.f("fk_event_type_version_event_type_id_event_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_type_version")),
        sa.UniqueConstraint("event_type_id", "ver", name=op.f("uq_event_type_version_event_type_id_ver")),
        comment="事件类型版本（只追加）",
    )

    for sql in enable_append_only("event_type_version"):
        op.execute(sql)


def downgrade() -> None:
    for sql in disable_append_only("event_type_version"):
        op.execute(sql)
    op.drop_table("event_type_version")
    op.drop_table("event_subscription")
    op.drop_table("event_payload_field")
    op.drop_table("event_type")
    op.drop_table("event_envelope_field")
