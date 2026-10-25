"""静态定义表：object_type / property / object_view / link_type / link_quality / metric /
dimension / dim_level / dim_value / dim_mapping / knowledge / knowledge_apply /
onto_qa_path / onto_qa_path_step / onto_qa_path_example（qa_ 前缀属问数模块，故加 onto_）

只建静态自己的表；外键只指向静态表；其它模块按 code 软引用。

Revision ID: static_0001
Revises: 0003
Create Date: 2026-10-25 22:40:28.590602+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "static_0001"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "term",
                "rule",
                "fiscal_calendar",
                "metric_note",
                name="knowledgetype",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "draft",
                "disabled",
                name="knowledgestatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("calc", sa.Text(), nullable=False),
        sa.Column(
            "synonyms",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("period", sa.String(length=128), nullable=False),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("maps", sa.Text(), nullable=False),
        sa.Column(
            "filters",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "tries",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "periods",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "refs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "history",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "draft",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="[推断] 草稿内容（应进 cr_item）",
        ),
        sa.Column("since_release", sa.String(length=32), nullable=True),
        sa.Column("stopped_release", sa.String(length=32), nullable=True, comment="mock stoppedIn"),
        sa.Column("cr_code", sa.String(length=32), nullable=True),
        sa.Column("content_updated_at", sa.DateTime(timezone=True), nullable=False, comment="mock updatedAt"),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge")),
        sa.UniqueConstraint("code", name=op.f("uq_knowledge_code")),
        comment="业务知识 / 术语",
    )
    op.create_table(
        "metric",
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("metric_group", sa.String(length=32), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False, comment="口径版本 v3"),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "draft",
                "offline",
                name="metricstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("review_cr", sa.String(length=32), nullable=True, comment="审核中的变更单号（review.cr）"),
        sa.Column("review_note", sa.String(length=256), nullable=True),
        sa.Column("formula", sa.Text(), nullable=True, comment="METRICS.formula（7 个指标有）"),
        sa.Column(
            "dims",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="允许拆分的维度名（METRICS.dims）",
        ),
        sa.Column(
            "value", sa.Numeric(precision=20, scale=4), nullable=True, comment="当前值（运行时，不进快照）"
        ),
        sa.Column("prev", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column(
            "stat_updated_at", sa.DateTime(timezone=True), nullable=True, comment="METRICS.updatedAt（带秒）"
        ),
        sa.Column(
            "def_rows",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "dim_rows",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "version_rows",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "ref_rows",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "disallowed_rows",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="不允许的维度 {dim,reason}（Metrics.vue DENY）",
        ),
        sa.Column("seq", sa.Integer(), nullable=False, comment="METRIC_LIST 顺序"),
        sa.Column("stat_seq", sa.Integer(), nullable=True, comment="METRICS 顺序（有统计值的 7 个）"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric")),
        sa.UniqueConstraint("api_name", name=op.f("uq_metric_api_name")),
        sa.UniqueConstraint("code", name=op.f("uq_metric_code")),
        comment="指标（当前版本）",
    )
    op.create_table(
        "object_type",
        sa.Column("api_name", sa.String(length=64), nullable=False, comment="API 名（SalesOrder）"),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("obj_group", sa.String(length=32), nullable=False, comment="分组（交易/客户/…）"),
        sa.Column("domain", sa.String(length=64), nullable=True, comment="业务域（零售交易）"),
        sa.Column("description", sa.Text(), nullable=False, comment="说明（自然语言）"),
        sa.Column(
            "conflict_strategy",
            sa.Enum(
                "user_edit_wins",
                "latest_source_ts",
                name="conflictstrategy",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "conflict_note",
            sa.String(length=128),
            nullable=True,
            comment="冲突策略括号里的补充说明（mock 原文去掉主值后的部分）",
        ),
        sa.Column(
            "lifecycle",
            sa.Enum("active", name="lifecycle", native_enum=False, create_constraint=True, length=16),
            nullable=False,
        ),
        sa.Column("sync", sa.String(length=128), nullable=True, comment="同步方式（文案）"),
        sa.Column(
            "sample_questions",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="样例问题 qs[{q,cert,query?}]",
        ),
        sa.Column("qa_cards", sa.Integer(), nullable=False, comment="问数卡片数（cards）"),
        sa.Column("qa30", sa.Integer(), nullable=False, comment="近 30 天问数次数"),
        sa.Column("unused_props", sa.Integer(), nullable=False, comment="未使用属性数"),
        sa.Column(
            "source_table", sa.String(length=128), nullable=True, comment="物理表（LINK_OBJECTS.table）"
        ),
        sa.Column(
            "datasource_name", sa.String(length=64), nullable=True, comment="数据源（Q_OBJECT_TYPES.source）"
        ),
        sa.Column("owner", sa.String(length=64), nullable=True, comment="负责人姓名（软引用）"),
        sa.Column(
            "pk_property", sa.String(length=64), nullable=True, comment="主键属性 api（Q_OBJECT_TYPES.pk）"
        ),
        sa.Column("record_count", sa.BigInteger(), nullable=True, comment="行数（运行时，不进快照）"),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=True, comment="数据截止时间"),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "draft",
                name="objectstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="published",
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False, comment="mock 顺序"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_type")),
        sa.UniqueConstraint("api_name", name=op.f("uq_object_type_api_name")),
        sa.UniqueConstraint("code", name=op.f("uq_object_type_code")),
        comment="对象类型",
    )
    op.create_table(
        "dimension",
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "dimension", "codelist", name="dimkind", native_enum=False, create_constraint=True, length=16
            ),
            nullable=False,
        ),
        sa.Column("dim_group", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "draft",
                "rejected",
                name="defstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.BigInteger(), nullable=False),
        sa.Column("object_name", sa.String(length=64), nullable=False, comment="mock objectName（原文）"),
        sa.Column("attr", sa.String(length=128), nullable=False),
        sa.Column("source_column", sa.String(length=256), nullable=False, comment="mock column"),
        sa.Column("value_src", sa.String(length=256), nullable=False),
        sa.Column("sync", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "qa",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="问数默认口径（原样）",
        ),
        sa.Column(
            "levels_before",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="[推断] 某版本之前的层级/口径（mock levelsBefore 原样）",
        ),
        sa.Column(
            "metric_dims",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "metric_note",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "usage",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "history",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("since_release", sa.String(length=32), nullable=False),
        sa.Column("count_label", sa.String(length=64), nullable=True),
        sa.Column("values_note", sa.Text(), nullable=True),
        sa.Column("draft_note", sa.Text(), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["object_id"], ["object_type.id"], name=op.f("fk_dimension_object_id_object_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dimension")),
        sa.UniqueConstraint("api_name", name=op.f("uq_dimension_api_name")),
        sa.UniqueConstraint("code", name=op.f("uq_dimension_code")),
        comment="维度 / 码表",
    )
    op.create_index(op.f("ix_dimension_object_id"), "dimension", ["object_id"], unique=False)
    op.create_table(
        "knowledge_apply",
        sa.Column("knowledge_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "target_kind",
            sa.Enum(
                "object_type",
                "metric",
                name="applykind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("target_code", sa.String(length=64), nullable=False),
        sa.Column("target_name", sa.String(length=128), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_id"],
            ["knowledge.id"],
            name=op.f("fk_knowledge_apply_knowledge_id_knowledge"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_apply")),
        sa.UniqueConstraint(
            "knowledge_id",
            "target_kind",
            "target_code",
            name=op.f("uq_knowledge_apply_knowledge_id_target_kind_target_code"),
        ),
        comment="知识适用范围",
    )
    op.create_index(
        op.f("ix_knowledge_apply_knowledge_id"), "knowledge_apply", ["knowledge_id"], unique=False
    )
    op.create_table(
        "link_type",
        sa.Column("api_name", sa.String(length=64), nullable=True, comment="API 名（mock api，缺省 = id）"),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("from_code", sa.String(length=64), nullable=False, comment="源对象前端 id"),
        sa.Column("to_code", sa.String(length=64), nullable=False, comment="目标对象前端 id"),
        sa.Column("from_object_id", sa.BigInteger(), nullable=True),
        sa.Column("to_object_id", sa.BigInteger(), nullable=True, comment="[推断] 目标还不是对象类型时为空"),
        sa.Column(
            "cardinality",
            sa.Enum(
                "one_to_one",
                "one_to_many",
                "many_to_one",
                "many_to_many",
                name="cardinality",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("key_expr", sa.String(length=256), nullable=False, comment="关联键（mock key）"),
        sa.Column("from_prop", sa.String(length=64), nullable=False),
        sa.Column("to_prop", sa.String(length=64), nullable=False),
        sa.Column("via", sa.String(length=64), nullable=True, comment="中间表"),
        sa.Column("join_sql", sa.Text(), nullable=False),
        sa.Column("reverse_name", sa.String(length=64), nullable=False),
        sa.Column("nullable", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "published",
                "draft",
                "rejected",
                name="defstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "since_release", sa.String(length=32), nullable=True, comment="首次发布版本号 [推断] 字符串"
        ),
        sa.Column("cr_code", sa.String(length=32), nullable=True, comment="变更单号（软引用）"),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column(
            "usage",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="引用（mock 原样）",
        ),
        sa.Column(
            "history",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="变更历史",
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["from_object_id"], ["object_type.id"], name=op.f("fk_link_type_from_object_id_object_type")
        ),
        sa.ForeignKeyConstraint(
            ["to_object_id"], ["object_type.id"], name=op.f("fk_link_type_to_object_id_object_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_link_type")),
        sa.UniqueConstraint("code", name=op.f("uq_link_type_code")),
        comment="关系类型",
    )
    op.create_index(op.f("ix_link_type_from_code"), "link_type", ["from_code"], unique=False)
    op.create_index(op.f("ix_link_type_from_object_id"), "link_type", ["from_object_id"], unique=False)
    op.create_index(op.f("ix_link_type_to_code"), "link_type", ["to_code"], unique=False)
    op.create_index(op.f("ix_link_type_to_object_id"), "link_type", ["to_object_id"], unique=False)
    op.create_table(
        "object_view",
        sa.Column("code", sa.String(length=64), nullable=False, comment="视图编号（前端 id）"),
        sa.Column("object_type_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "workbench",
                "my_page",
                "object_list",
                name="viewkind",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("owner", sa.String(length=64), nullable=False, comment="创建人姓名（软引用）"),
        sa.Column(
            "visibility",
            sa.Enum(
                "only_me",
                "specified",
                "by_role",
                "everyone",
                name="viewvisibility",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("visible_to", sa.String(length=256), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uses_30d", sa.Integer(), nullable=False),
        sa.Column("filter_text", sa.Text(), nullable=False),
        sa.Column(
            "props",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["object_type_id"],
            ["object_type.id"],
            name=op.f("fk_object_view_object_type_id_object_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_view")),
        sa.UniqueConstraint("object_type_id", "code", name=op.f("uq_object_view_object_type_id_code")),
        comment="对象视图",
    )
    op.create_index(op.f("ix_object_view_code"), "object_view", ["code"], unique=False)
    op.create_index(op.f("ix_object_view_object_type_id"), "object_view", ["object_type_id"], unique=False)
    op.create_index(
        "uq_object_view_code_nonseed",
        "object_view",
        ["code"],
        unique=True,
        postgresql_where=sa.text("NOT is_seed"),
    )
    op.create_table(
        "property",
        sa.Column("object_type_id", sa.BigInteger(), nullable=False),
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column(
            "data_type", sa.String(length=32), nullable=False, comment="类型（string/decimal/…，原样）"
        ),
        sa.Column("prop_group", sa.String(length=32), nullable=False, comment="分组（基本/金额/…）"),
        sa.Column("is_pk", sa.Boolean(), nullable=False),
        sa.Column("is_title", sa.Boolean(), nullable=False),
        sa.Column("is_dimension", sa.Boolean(), nullable=False, comment="mock dim"),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False),
        sa.Column("enum_name", sa.String(length=64), nullable=True),
        sa.Column("source_column", sa.String(length=256), nullable=False, comment="来源字段（mock source）"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("src_type", sa.String(length=64), nullable=False, comment="映射页：源类型"),
        sa.Column("transform", sa.Text(), nullable=False, comment="映射页：转换说明"),
        sa.Column("map_column", sa.String(length=256), nullable=False, comment="映射页：源字段"),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["object_type_id"],
            ["object_type.id"],
            name=op.f("fk_property_object_type_id_object_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_property")),
        sa.UniqueConstraint("object_type_id", "api_name", name=op.f("uq_property_object_type_id_api_name")),
        comment="对象属性",
    )
    op.create_index(op.f("ix_property_object_type_id"), "property", ["object_type_id"], unique=False)
    op.create_table(
        "dim_level",
        sa.Column("dimension_id", sa.BigInteger(), nullable=False),
        sa.Column("level_no", sa.Integer(), nullable=False, comment="从 1 起"),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("api_name", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["dimension_id"],
            ["dimension.id"],
            name=op.f("fk_dim_level_dimension_id_dimension"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dim_level")),
        sa.UniqueConstraint("dimension_id", "level_no", name=op.f("uq_dim_level_dimension_id_level_no")),
        comment="维度层级",
    )
    op.create_table(
        "dim_mapping",
        sa.Column("dimension_id", sa.BigInteger(), nullable=False),
        sa.Column("map_key", sa.String(length=256), nullable=False, comment="mock key"),
        sa.Column("phrase", sa.String(length=128), nullable=False),
        sa.Column(
            "targets",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "match",
            sa.Enum(
                "exact",
                "contains",
                "multi",
                name="matchmode",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "source",
            sa.Enum(
                "value_synonym",
                "value_name",
                "knowledge",
                "qa_feedback",
                name="mappingsource",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("hits", sa.Integer(), nullable=False, comment="命中次数（运行时）"),
        sa.Column("conflict", sa.Text(), nullable=True),
        sa.Column("resolve", sa.Text(), nullable=True),
        sa.Column("draft_cr", sa.String(length=32), nullable=True),
        sa.Column("diff", sa.String(length=16), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["dimension_id"],
            ["dimension.id"],
            name=op.f("fk_dim_mapping_dimension_id_dimension"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dim_mapping")),
        sa.UniqueConstraint("dimension_id", "map_key", name=op.f("uq_dim_mapping_dimension_id_map_key")),
        comment="维度说法映射",
    )
    op.create_index(op.f("ix_dim_mapping_dimension_id"), "dim_mapping", ["dimension_id"], unique=False)
    op.create_table(
        "dim_value",
        sa.Column("dimension_id", sa.BigInteger(), nullable=False),
        sa.Column("value_key", sa.String(length=128), nullable=False, comment="mock key（层级:编码）"),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("level_name", sa.String(length=64), nullable=False),
        sa.Column("parent", sa.String(length=128), nullable=True, comment="上级取值名称（mock parent 原文）"),
        sa.Column(
            "status",
            sa.Enum(
                "enabled",
                "disabled",
                name="dimvaluestatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "synonyms",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("record_count", sa.BigInteger(), nullable=True, comment="mock count（运行时）"),
        sa.Column("src", sa.String(length=256), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "ref",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="跳转引用（mock ref 原样）",
        ),
        sa.Column("since_release", sa.String(length=32), nullable=True),
        sa.Column("until_release", sa.String(length=32), nullable=True),
        sa.Column(
            "draft_cr", sa.String(length=32), nullable=True, comment="[推断] 草稿所在变更单（mock draft）"
        ),
        sa.Column("diff", sa.String(length=16), nullable=True),
        sa.Column(
            "pub",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="草稿里改过的值的已发布原值（mock pub；当前种子没有）",
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["dimension_id"],
            ["dimension.id"],
            name=op.f("fk_dim_value_dimension_id_dimension"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dim_value")),
        sa.UniqueConstraint("dimension_id", "value_key", name=op.f("uq_dim_value_dimension_id_value_key")),
        comment="维度取值",
    )
    op.create_index(op.f("ix_dim_value_dimension_id"), "dim_value", ["dimension_id"], unique=False)
    op.create_table(
        "link_quality",
        sa.Column("link_type_id", sa.BigInteger(), nullable=False),
        sa.Column("hit", sa.Numeric(precision=7, scale=3), nullable=False, comment="命中率 %"),
        sa.Column("orphans", sa.BigInteger(), nullable=False),
        sa.Column("nulls", sa.BigInteger(), nullable=False),
        sa.Column("dup", sa.BigInteger(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "orphan_samples",
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
            ["link_type_id"],
            ["link_type.id"],
            name=op.f("fk_link_quality_link_type_id_link_type"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_link_quality")),
        sa.UniqueConstraint("link_type_id", name=op.f("uq_link_quality_link_type_id")),
        comment="关系数据质量",
    )
    op.create_table(
        "onto_qa_path",
        sa.Column("group_key", sa.String(length=160), nullable=False, comment="from→to（快照键）[推断]"),
        sa.Column("from_object_id", sa.BigInteger(), nullable=False),
        sa.Column("to_object_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, comment="组内序号（前端 paths 下标）"),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("hint", sa.String(length=256), nullable=True, comment="需指明路径时的提示词"),
        sa.Column(
            "check_note", sa.Text(), nullable=True, comment="组级 V13 校验说明，只存在默认路径行 [推断]"
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["from_object_id"], ["object_type.id"], name=op.f("fk_onto_qa_path_from_object_id_object_type")
        ),
        sa.ForeignKeyConstraint(
            ["to_object_id"], ["object_type.id"], name=op.f("fk_onto_qa_path_to_object_id_object_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onto_qa_path")),
        sa.UniqueConstraint(
            "from_object_id",
            "to_object_id",
            "seq",
            name=op.f("uq_onto_qa_path_from_object_id_to_object_id_seq"),
        ),
        sa.UniqueConstraint("group_key", "seq", name=op.f("uq_onto_qa_path_group_key_seq")),
        comment="问数路径",
    )
    op.create_index(
        "uq_onto_qa_path_default",
        "onto_qa_path",
        ["from_object_id", "to_object_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.create_table(
        "onto_qa_path_example",
        sa.Column("from_object_id", sa.BigInteger(), nullable=False),
        sa.Column("to_object_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("path_id", sa.BigInteger(), nullable=True, comment="空 = 先追问"),
        sa.Column("how", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["from_object_id"],
            ["object_type.id"],
            name=op.f("fk_onto_qa_path_example_from_object_id_object_type"),
        ),
        sa.ForeignKeyConstraint(
            ["path_id"],
            ["onto_qa_path.id"],
            name=op.f("fk_onto_qa_path_example_path_id_onto_qa_path"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_object_id"],
            ["object_type.id"],
            name=op.f("fk_onto_qa_path_example_to_object_id_object_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onto_qa_path_example")),
        sa.UniqueConstraint(
            "from_object_id",
            "to_object_id",
            "seq",
            name=op.f("uq_onto_qa_path_example_from_object_id_to_object_id_seq"),
        ),
        comment="问数路径试问",
    )
    op.create_index(
        op.f("ix_onto_qa_path_example_path_id"), "onto_qa_path_example", ["path_id"], unique=False
    )
    op.create_table(
        "onto_qa_path_step",
        sa.Column("path_id", sa.BigInteger(), nullable=False),
        sa.Column("step_no", sa.Integer(), nullable=False, comment="从 0 起"),
        sa.Column("link_type_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["link_type_id"], ["link_type.id"], name=op.f("fk_onto_qa_path_step_link_type_id_link_type")
        ),
        sa.ForeignKeyConstraint(
            ["path_id"],
            ["onto_qa_path.id"],
            name=op.f("fk_onto_qa_path_step_path_id_onto_qa_path"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_onto_qa_path_step")),
        sa.UniqueConstraint("path_id", "step_no", name=op.f("uq_onto_qa_path_step_path_id_step_no")),
        comment="问数路径步骤",
    )
    op.create_index(
        op.f("ix_onto_qa_path_step_link_type_id"), "onto_qa_path_step", ["link_type_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_onto_qa_path_step_link_type_id"), table_name="onto_qa_path_step")
    op.drop_table("onto_qa_path_step")
    op.drop_index(op.f("ix_onto_qa_path_example_path_id"), table_name="onto_qa_path_example")
    op.drop_table("onto_qa_path_example")
    op.drop_index(
        "uq_onto_qa_path_default", table_name="onto_qa_path", postgresql_where=sa.text("is_default")
    )
    op.drop_table("onto_qa_path")
    op.drop_table("link_quality")
    op.drop_index(op.f("ix_dim_value_dimension_id"), table_name="dim_value")
    op.drop_table("dim_value")
    op.drop_index(op.f("ix_dim_mapping_dimension_id"), table_name="dim_mapping")
    op.drop_table("dim_mapping")
    op.drop_table("dim_level")
    op.drop_index(op.f("ix_property_object_type_id"), table_name="property")
    op.drop_table("property")
    op.drop_index(
        "uq_object_view_code_nonseed", table_name="object_view", postgresql_where=sa.text("NOT is_seed")
    )
    op.drop_index(op.f("ix_object_view_object_type_id"), table_name="object_view")
    op.drop_index(op.f("ix_object_view_code"), table_name="object_view")
    op.drop_table("object_view")
    op.drop_index(op.f("ix_link_type_to_object_id"), table_name="link_type")
    op.drop_index(op.f("ix_link_type_to_code"), table_name="link_type")
    op.drop_index(op.f("ix_link_type_from_object_id"), table_name="link_type")
    op.drop_index(op.f("ix_link_type_from_code"), table_name="link_type")
    op.drop_table("link_type")
    op.drop_index(op.f("ix_knowledge_apply_knowledge_id"), table_name="knowledge_apply")
    op.drop_table("knowledge_apply")
    op.drop_index(op.f("ix_dimension_object_id"), table_name="dimension")
    op.drop_table("dimension")
    op.drop_table("object_type")
    op.drop_table("metric")
    op.drop_table("knowledge")
