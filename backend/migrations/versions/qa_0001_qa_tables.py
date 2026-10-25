"""qa 模块第一批表（qa-api.md §9.2）：会话/轮次/事件/常用问题/权限申请、页面/卡片/共享/订阅、模板、
认证问答/同问法、评测题/运行/失败明细、反馈、高频问题、同义词、对象展示定义、查询日志。

- 外键只指向骨架已有的 sys_user 和 qa 自己的表（release_id 按 id 软引用 ontology_release）；
  静态 metric / change_request、
  动态 action_run 尚未合入，按 code 软引用（metric_code + metric_version、cr_code）。
- 只追加：qa_eval_fail、qa_object_type_view、qa_query_log 挂 append-only 触发器。

Revision ID: qa_0001
Revises: 0003
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.ddl import disable_append_only, enable_append_only

APPEND_ONLY = ("qa_eval_fail", "qa_object_type_view", "qa_query_log")

revision: str = "qa_0001"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_cert",
        sa.Column("question", sa.String(length=40), nullable=False),
        sa.Column(
            "sem_plan",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("metric_code", sa.String(length=64), nullable=True),
        sa.Column("metric_version", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "certified",
                "recert",
                "disabled",
                name="certstatus",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("cr_code", sa.String(length=32), nullable=True, comment="变更单编号软引用"),
        sa.Column("certifier", sa.String(length=64), nullable=True, comment="认证人姓名软引用"),
        sa.Column("certified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_cert")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_cert_code")),
        comment="认证问答",
    )
    op.create_index("ix_qa_cert_status", "qa_cert", ["status"], unique=False)
    op.create_index(
        "uq_qa_cert_lower_question", "qa_cert", [sa.literal_column("lower(question)")], unique=True
    )
    op.create_table(
        "qa_eval_question",
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("group_name", sa.String(length=8), nullable=True, comment="分组（中文，sql 题为空）"),
        sa.Column(
            "kind",
            sa.Enum("semplan", "sql", name="evalkind", native_enum=False, create_constraint=True, length=8),
            server_default="semplan",
            nullable=False,
        ),
        sa.Column("gold_sql", sa.Text(), nullable=True),
        sa.Column(
            "expect",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="期望 SemPlan（semplan 题）",
        ),
        sa.Column(
            "meta",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="[推断] sql 题的其余字段（expected/category/tags…）",
        ),
        sa.Column(
            "last_result",
            sa.Enum("pass", "fail", name="evallast", native_enum=False, create_constraint=True, length=8),
            nullable=True,
        ),
        sa.Column("cr_code", sa.String(length=32), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "group_name IS NULL OR group_name IN ('销售', '退款', '履约', '毛利', '复购', '订单')",
            name=op.f("ck_qa_eval_question_group_name"),
        ),
        sa.CheckConstraint(
            "kind <> 'sql' OR gold_sql IS NOT NULL", name=op.f("ck_qa_eval_question_sql_has_gold")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_eval_question")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_eval_question_code")),
        sa.UniqueConstraint("question", name=op.f("uq_qa_eval_question_question")),
        comment="评测题",
    )
    op.create_index(
        "ix_qa_eval_question_kind_group_name", "qa_eval_question", ["kind", "group_name"], unique=False
    )
    op.create_table(
        "qa_eval_run",
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_label", sa.String(length=16), nullable=True),
        sa.Column("trigger_label", sa.String(length=40), nullable=False),
        sa.Column("version_label", sa.String(length=32), nullable=True, comment="[推断] mock version 原文"),
        sa.Column("scope", sa.String(length=60), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "done",
                name="runstatus",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column("operator", sa.String(length=64), nullable=False, comment="操作人软引用（含「系统」）"),
        sa.Column("cr_code", sa.String(length=32), nullable=True),
        sa.Column(
            "crs", sa.JSON().with_variant(postgresql.ARRAY(sa.String(length=32)), "postgresql"), nullable=True
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "release_id", sa.BigInteger(), nullable=True, comment="本体版本（ontology_release.id，软引用）"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_eval_run")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_eval_run_code")),
        comment="评测运行",
    )
    op.create_index(op.f("ix_qa_eval_run_release_id"), "qa_eval_run", ["release_id"], unique=False)
    op.create_index("ix_qa_eval_run_run_at", "qa_eval_run", ["run_at"], unique=False)
    op.create_table(
        "qa_object_type_view",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=16), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=True),
        sa.Column("kw_placeholder", sa.String(length=64), nullable=True),
        sa.Column(
            "facet_defs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "column_defs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "field_groups",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "kpis",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "quick_questions",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "release_id", sa.BigInteger(), nullable=True, comment="本体版本（ontology_release.id，软引用）"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_object_type_view")),
        sa.UniqueConstraint(
            "kind",
            "release_id",
            name=op.f("uq_qa_object_type_view_kind_release_id"),
            postgresql_nulls_not_distinct=True,
        ),
        comment="问数对象类型展示定义（只追加）",
    )
    op.create_index(
        op.f("ix_qa_object_type_view_release_id"), "qa_object_type_view", ["release_id"], unique=False
    )
    op.create_table(
        "qa_session_option",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("option_value", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=60), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("kind IN ('scope', 'caliber')", name=op.f("ck_qa_session_option_kind")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_session_option")),
        sa.UniqueConstraint("kind", "option_value", name=op.f("uq_qa_session_option_kind_option_value")),
        comment="问数会话选项（范围 / 口径）",
    )
    op.create_table(
        "qa_synonym_link",
        sa.Column("word", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("cr_code", sa.String(length=32), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_synonym_link")),
        sa.UniqueConstraint(
            "word",
            "cr_code",
            name=op.f("uq_qa_synonym_link_word_cr_code"),
            postgresql_nulls_not_distinct=True,
        ),
        comment="已走变更单的同义词",
    )
    op.create_table(
        "qa_template",
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("scene", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, comment="提供方软引用"),
        sa.Column("official", sa.Boolean(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_template")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_template_code")),
        sa.UniqueConstraint("name", name=op.f("uq_qa_template_name")),
        comment="页面模板",
    )
    op.create_index("ix_qa_template_scene", "qa_template", ["scene"], unique=False)
    op.create_table(
        "qa_cert_variant",
        sa.Column("cert_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.String(length=40), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["cert_id"], ["qa_cert.id"], name=op.f("fk_qa_cert_variant_cert_id_qa_cert"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_cert_variant")),
        sa.UniqueConstraint("cert_id", "question", name=op.f("uq_qa_cert_variant_cert_id_question")),
        comment="认证问答同问法",
    )
    op.create_index(
        "uq_qa_cert_variant_lower_question",
        "qa_cert_variant",
        [sa.literal_column("lower(question)")],
        unique=True,
    )
    op.create_table(
        "qa_eval_fail",
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("question_id", sa.BigInteger(), nullable=False),
        sa.Column("actual", sa.Text(), nullable=False, comment="[推断] 实际结果文字（mock 是字符串）"),
        sa.Column(
            "kind",
            sa.Enum(
                "metric_not_recalled",
                "wrong_metric",
                "dim_mismatch",
                "time_parse",
                "missing_filter",
                name="failkind",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["qa_eval_question.id"],
            name=op.f("fk_qa_eval_fail_question_id_qa_eval_question"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["qa_eval_run.id"], name=op.f("fk_qa_eval_fail_run_id_qa_eval_run")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_eval_fail")),
        sa.UniqueConstraint("run_id", "question_id", name=op.f("uq_qa_eval_fail_run_id_question_id")),
        comment="评测失败明细（只追加）",
    )
    op.create_index("ix_qa_eval_fail_run_id_kind", "qa_eval_fail", ["run_id", "kind"], unique=False)
    op.create_table(
        "qa_favorite",
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["sys_user.id"], name=op.f("fk_qa_favorite_owner_id_sys_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_favorite")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_favorite_code")),
        sa.UniqueConstraint("owner_id", "question", name=op.f("uq_qa_favorite_owner_id_question")),
        comment="常用问题",
    )
    op.create_index("ix_qa_favorite_owner_id_uses", "qa_favorite", ["owner_id", "uses"], unique=False)
    op.create_table(
        "qa_freq_question",
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("hits7", sa.Integer(), nullable=False),
        sa.Column("hits30", sa.Integer(), nullable=False),
        sa.Column("users", sa.Integer(), nullable=False),
        sa.Column(
            "sem_plan",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("consistency", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("last_asked", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cert_id", sa.BigInteger(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["cert_id"], ["qa_cert.id"], name=op.f("fk_qa_freq_question_cert_id_qa_cert"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_freq_question")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_freq_question_code")),
        sa.UniqueConstraint("question", name=op.f("uq_qa_freq_question_question")),
        comment="高频问题（物化统计）",
    )
    op.create_table(
        "qa_page",
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("scope", sa.String(length=60), nullable=True),
        sa.Column(
            "source_kind",
            sa.Enum(
                "workbench",
                "template",
                "manual",
                name="pagesourcekind",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("source_ref", sa.String(length=200), nullable=True, comment="来源原文（mock source）"),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "refresh_failed",
                "caliber_pending",
                name="pagestatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="ok",
            nullable=False,
        ),
        sa.Column("status_note", sa.Text(), nullable=True),
        sa.Column(
            "refresh",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="RefreshPlan（接口值原样存）",
        ),
        sa.Column("next_run", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "share_mode",
            sa.Enum(
                "private",
                "members",
                "dept",
                name="sharemode",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            server_default="private",
            nullable=False,
        ),
        sa.Column("share_link", sa.Boolean(), nullable=False),
        sa.Column("allow_export", sa.Boolean(), nullable=False),
        sa.Column("link_token", sa.String(length=64), nullable=True),
        sa.Column("views", sa.Integer(), nullable=False, comment="[推断] 浏览次数（mock views）"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["sys_user.id"], name=op.f("fk_qa_page_owner_id_sys_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_page")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_page_code")),
        sa.UniqueConstraint("owner_id", "name", name=op.f("uq_qa_page_owner_id_name")),
        comment="我的页面",
    )
    op.create_index("ix_qa_page_next_run", "qa_page", ["next_run"], unique=False)
    op.create_index("ix_qa_page_owner_id_updated_at", "qa_page", ["owner_id", "updated_at"], unique=False)
    op.create_table(
        "qa_query_log",
        sa.Column("turn_id", sa.BigInteger(), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("norm_question", sa.Text(), nullable=False),
        sa.Column(
            "sem_plan",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("plan_hash", sa.String(length=64), nullable=True),
        sa.Column("metric_code", sa.String(length=64), nullable=True),
        sa.Column("metric_version", sa.Integer(), nullable=True),
        sa.Column("cert_id", sa.BigInteger(), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], name=op.f("fk_qa_query_log_user_id_sys_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_query_log")),
        comment="问数查询日志（只追加）",
    )
    op.create_index("ix_qa_query_log_asked_at", "qa_query_log", ["asked_at"], unique=False)
    op.create_index(
        "ix_qa_query_log_norm_question_asked_at", "qa_query_log", ["norm_question", "asked_at"], unique=False
    )
    op.create_table(
        "qa_session",
        sa.Column("owner_id", sa.BigInteger(), nullable=False, comment="拥有者（sys_user.id）"),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column(
            "scope", sa.String(length=60), nullable=True, comment="范围（QA_SCOPE_OPTIONS.value，如 华东）"
        ),
        sa.Column(
            "caliber",
            sa.Enum("ex", "in", name="sessioncaliber", native_enum=False, create_constraint=True, length=8),
            server_default="ex",
            nullable=False,
            comment="会话口径 ex/in（接口 不含税/含税）",
        ),
        sa.Column(
            "last_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="最近活动时间（接口 date 只取日期）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["sys_user.id"], name=op.f("fk_qa_session_owner_id_sys_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_session")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_session_code")),
        comment="问数会话",
    )
    op.create_index("ix_qa_session_owner_id_last_at", "qa_session", ["owner_id", "last_at"], unique=False)
    op.create_table(
        "qa_template_card",
        sa.Column("template_id", sa.BigInteger(), nullable=False),
        sa.Column("card_key", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column(
            "viz",
            sa.Enum(
                "kpi",
                "line",
                "bar",
                "table",
                name="cardviz",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column(
            "sem_plan",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "metric_name", sa.String(length=64), nullable=True, comment="指标名快照（mock card.metric）"
        ),
        sa.Column("metric_code", sa.String(length=64), nullable=True),
        sa.Column("metric_version", sa.Integer(), nullable=True),
        sa.Column("pending_cr", sa.Text(), nullable=True),
        sa.Column(
            "result_cache",
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
            ["template_id"],
            ["qa_template.id"],
            name=op.f("fk_qa_template_card_template_id_qa_template"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_template_card")),
        sa.UniqueConstraint("template_id", "card_key", name=op.f("uq_qa_template_card_template_id_card_key")),
        comment="模板卡片",
    )
    op.create_table(
        "qa_page_card",
        sa.Column("page_id", sa.BigInteger(), nullable=False),
        sa.Column("card_key", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=60), nullable=False),
        sa.Column(
            "viz",
            sa.Enum(
                "kpi",
                "line",
                "bar",
                "table",
                name="cardviz",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column(
            "sem_plan",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="冻结的语义计划（mock 卡片没有，种子为空）",
        ),
        sa.Column(
            "metric_name", sa.String(length=64), nullable=True, comment="指标名快照（mock card.metric）"
        ),
        sa.Column("metric_code", sa.String(length=64), nullable=True),
        sa.Column("metric_version", sa.Integer(), nullable=True),
        sa.Column("pending_cr", sa.Text(), nullable=True, comment="口径待更新说明（mock pending）"),
        sa.Column("source_turn_id", sa.BigInteger(), nullable=True, comment="来源轮次（不加外键）"),
        sa.Column(
            "result_cache",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            comment="value/unit/sub/series/labels/rows",
        ),
        sa.Column("perm_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["page_id"], ["qa_page.id"], name=op.f("fk_qa_page_card_page_id_qa_page"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_page_card")),
        sa.UniqueConstraint("page_id", "card_key", name=op.f("uq_qa_page_card_page_id_card_key")),
        comment="页面卡片",
    )
    op.create_table(
        "qa_page_share_member",
        sa.Column("page_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "perm",
            sa.Enum("view", "edit", name="shareperm", native_enum=False, create_constraint=True, length=8),
            nullable=False,
        ),
        sa.Column("dept", sa.String(length=64), nullable=True, comment="[推断] 部门快照（ShareMember.dept）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["qa_page.id"],
            name=op.f("fk_qa_page_share_member_page_id_qa_page"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["sys_user.id"], name=op.f("fk_qa_page_share_member_user_id_sys_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_page_share_member")),
        sa.UniqueConstraint("page_id", "user_id", name=op.f("uq_qa_page_share_member_page_id_user_id")),
        comment="页面共享成员",
    )
    op.create_table(
        "qa_page_subscription",
        sa.Column("page_id", sa.BigInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "channel",
            sa.Enum(
                "email",
                "wecom",
                "inbox",
                name="subchannel",
                native_enum=False,
                create_constraint=True,
                length=8,
            ),
            nullable=False,
        ),
        sa.Column(
            "recipients",
            sa.JSON().with_variant(postgresql.ARRAY(sa.String(length=64)), "postgresql"),
            nullable=False,
            comment="人名软引用",
        ),
        sa.Column(
            "send_when",
            sa.Enum(
                "after_refresh",
                "weekly_mon_0900",
                "daily_0900",
                name="subwhen",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("only_changed", sa.Boolean(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["page_id"],
            ["qa_page.id"],
            name=op.f("fk_qa_page_subscription_page_id_qa_page"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_page_subscription")),
        sa.UniqueConstraint("page_id", name=op.f("uq_qa_page_subscription_page_id")),
        comment="页面订阅",
    )
    op.create_table(
        "qa_turn",
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "answer",
                "denied",
                "empty",
                "system",
                "failed",
                name="turnkind",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column(
            "refs",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "sem_plan",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
            comment="语义计划 SemPlan",
        ),
        sa.Column(
            "snapshot",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "metric_code", sa.String(length=64), nullable=True, comment="[推断] 指标软引用（metric 未合入）"
        ),
        sa.Column("metric_version", sa.Integer(), nullable=True),
        sa.Column(
            "answered_ver",
            sa.String(length=32),
            nullable=True,
            comment="[推断] 回答时的版本号文字（种子环境 ontology_release 可能为空）",
        ),
        sa.Column("sql_text", sa.Text(), nullable=True, comment="执行的 SQL（不出接口）"),
        sa.Column(
            "status",
            sa.Enum(
                "streaming",
                "done",
                "failed",
                name="turnstatus",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            server_default="done",
            nullable=False,
        ),
        sa.Column("vote", sa.String(length=4), server_default="", nullable=False),
        sa.Column("caliber_variant", sa.String(length=8), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "release_id", sa.BigInteger(), nullable=True, comment="本体版本（ontology_release.id，软引用）"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["qa_session.id"],
            name=op.f("fk_qa_turn_session_id_qa_session"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_turn")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_turn_code")),
        sa.UniqueConstraint("session_id", "seq", name=op.f("uq_qa_turn_session_id_seq")),
        comment="问数轮次",
    )
    op.create_index(op.f("ix_qa_turn_release_id"), "qa_turn", ["release_id"], unique=False)
    op.create_table(
        "qa_feedback",
        sa.Column("turn_id", sa.BigInteger(), nullable=True),
        sa.Column("fed_back_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("user_name", sa.String(length=64), nullable=False),
        sa.Column("dept", sa.String(length=64), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "fix_kind",
            sa.Enum("synonym", "cert", name="fixkind", native_enum=False, create_constraint=True, length=8),
            nullable=False,
        ),
        sa.Column("fix_text", sa.String(length=64), nullable=False),
        sa.Column("fix_target", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "to_synonym",
                "to_cert",
                "await_release",
                "ignored",
                name="fbstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("cr_code", sa.String(length=32), nullable=True),
        sa.Column("cert_id", sa.BigInteger(), nullable=True),
        sa.Column("handler", sa.String(length=64), nullable=True, comment="处理人软引用"),
        sa.Column("resolved_by", sa.String(length=32), nullable=True, comment="CR 编号软引用（不是人）"),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["cert_id"], ["qa_cert.id"], name=op.f("fk_qa_feedback_cert_id_qa_cert")),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["qa_turn.id"], name=op.f("fk_qa_feedback_turn_id_qa_turn"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], name=op.f("fk_qa_feedback_user_id_sys_user")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_feedback")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_feedback_code")),
        comment="反馈与纠错",
    )
    op.create_index("ix_qa_feedback_fix_text", "qa_feedback", ["fix_text"], unique=False)
    op.create_index(
        "ix_qa_feedback_status_fed_back_at", "qa_feedback", ["status", "fed_back_at"], unique=False
    )
    op.create_table(
        "qa_permission_request",
        sa.Column("requester_id", sa.BigInteger(), nullable=False),
        sa.Column("scope", sa.String(length=60), nullable=False),
        sa.Column("object_label", sa.String(length=60), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("source_turn_id", sa.BigInteger(), nullable=True),
        sa.Column("approver", sa.String(length=64), nullable=True, comment="审批人姓名软引用"),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                name="permrequeststatus",
                native_enum=False,
                create_constraint=True,
                length=12,
            ),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "duration_days IN (30, 90, 180)", name=op.f("ck_qa_permission_request_duration_days")
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["sys_user.id"], name=op.f("fk_qa_permission_request_requester_id_sys_user")
        ),
        sa.ForeignKeyConstraint(
            ["source_turn_id"],
            ["qa_turn.id"],
            name=op.f("fk_qa_permission_request_source_turn_id_qa_turn"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_permission_request")),
        sa.UniqueConstraint("code", name=op.f("uq_qa_permission_request_code")),
        comment="数据权限申请（W16）",
    )
    op.create_index(
        "ix_qa_permission_request_requester_id_status",
        "qa_permission_request",
        ["requester_id", "status"],
        unique=False,
    )
    op.create_table(
        "qa_turn_event",
        sa.Column("turn_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_name", sa.String(length=16), nullable=False),
        sa.Column(
            "payload",
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
            ["turn_id"], ["qa_turn.id"], name=op.f("fk_qa_turn_event_turn_id_qa_turn"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qa_turn_event")),
        sa.UniqueConstraint("turn_id", "seq", name=op.f("uq_qa_turn_event_turn_id_seq")),
        comment="问数轮次事件",
    )
    for table in APPEND_ONLY:
        for sql in enable_append_only(table):
            op.execute(sql)


def downgrade() -> None:
    for table in APPEND_ONLY:
        for sql in disable_append_only(table):
            op.execute(sql)
    op.drop_table("qa_turn_event")
    op.drop_index("ix_qa_permission_request_requester_id_status", table_name="qa_permission_request")
    op.drop_table("qa_permission_request")
    op.drop_index("ix_qa_feedback_status_fed_back_at", table_name="qa_feedback")
    op.drop_index("ix_qa_feedback_fix_text", table_name="qa_feedback")
    op.drop_table("qa_feedback")
    op.drop_index(op.f("ix_qa_turn_release_id"), table_name="qa_turn")
    op.drop_table("qa_turn")
    op.drop_table("qa_page_subscription")
    op.drop_table("qa_page_share_member")
    op.drop_table("qa_page_card")
    op.drop_table("qa_template_card")
    op.drop_index("ix_qa_session_owner_id_last_at", table_name="qa_session")
    op.drop_table("qa_session")
    op.drop_index("ix_qa_query_log_norm_question_asked_at", table_name="qa_query_log")
    op.drop_index("ix_qa_query_log_asked_at", table_name="qa_query_log")
    op.drop_table("qa_query_log")
    op.drop_index("ix_qa_page_owner_id_updated_at", table_name="qa_page")
    op.drop_index("ix_qa_page_next_run", table_name="qa_page")
    op.drop_table("qa_page")
    op.drop_table("qa_freq_question")
    op.drop_index("ix_qa_favorite_owner_id_uses", table_name="qa_favorite")
    op.drop_table("qa_favorite")
    op.drop_index("ix_qa_eval_fail_run_id_kind", table_name="qa_eval_fail")
    op.drop_table("qa_eval_fail")
    op.drop_index("uq_qa_cert_variant_lower_question", table_name="qa_cert_variant")
    op.drop_table("qa_cert_variant")
    op.drop_index("ix_qa_template_scene", table_name="qa_template")
    op.drop_table("qa_template")
    op.drop_table("qa_synonym_link")
    op.drop_table("qa_session_option")
    op.drop_index(op.f("ix_qa_object_type_view_release_id"), table_name="qa_object_type_view")
    op.drop_table("qa_object_type_view")
    op.drop_index("ix_qa_eval_run_run_at", table_name="qa_eval_run")
    op.drop_index(op.f("ix_qa_eval_run_release_id"), table_name="qa_eval_run")
    op.drop_table("qa_eval_run")
    op.drop_index("ix_qa_eval_question_kind_group_name", table_name="qa_eval_question")
    op.drop_table("qa_eval_question")
    op.drop_index("uq_qa_cert_lower_question", table_name="qa_cert")
    op.drop_index("ix_qa_cert_status", table_name="qa_cert")
    op.drop_table("qa_cert")
