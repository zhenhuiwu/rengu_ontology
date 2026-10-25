"""动态本体：告警 alert / alert_log + 动作执行 action_run / action_run_log

外键只指向本模块自己的表；规则、命中、推演、动作类型、外部系统等未合并模块用业务编号软引用
（见 docs/backend/dynamic-impl-notes.md「FK to add later」）。
alert.action_run_id ↔ action_run.alert_id 互指，alert 的外键在 action_run 建好后再加。
alert_log / action_run_log 只追加（数据库触发器兜底）。

Revision ID: dynamic_0001
Revises: static_0001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.ddl import disable_append_only, enable_append_only

revision: str = "dynamic_0001"
down_revision: str | None = "static_0001"  # 合并方按当时的 master head 再改
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY = ("alert_log", "action_run_log")


def upgrade() -> None:
    op.create_table(
        "alert",
        sa.Column(
            "rule_code",
            sa.String(length=64),
            nullable=False,
            comment="规则编号（软引用 rule.code，FK to add later）",
        ),
        sa.Column("rule_version", sa.String(length=16), nullable=True, comment="命中时的规则版本（v3）"),
        sa.Column(
            "hit_code",
            sa.String(length=64),
            nullable=True,
            comment="命中记录编号（软引用 rule_hit，FK to add later）",
        ),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
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
            "obj_kind", sa.String(length=32), nullable=False, comment="product/store/order（软引用对象类型）"
        ),
        sa.Column("obj_id", sa.String(length=64), nullable=False),
        sa.Column("obj_label", sa.String(length=200), nullable=False),
        sa.Column("store_id", sa.String(length=64), nullable=True),
        sa.Column("store_label", sa.String(length=200), nullable=True),
        sa.Column("value", sa.String(length=200), nullable=False, comment="命中值（展示文本）"),
        sa.Column("threshold", sa.String(length=200), nullable=False, comment="阈值（展示文本）"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "processing",
                "resolved",
                "ignored",
                name="alertstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("assignee", sa.String(length=64), nullable=True, comment="处理人姓名"),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column(
            "action_run_id",
            sa.BigInteger(),
            nullable=True,
            comment="关联动作（action_run.id；和 action_run.alert_id 互指，建表后再加约束）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert")),
        sa.UniqueConstraint("code", name=op.f("uq_alert_code")),
        comment="告警（规则命中生成，人工处理）",
    )
    op.create_index(op.f("ix_alert_rule_code"), "alert", ["rule_code"], unique=False)
    op.create_index("ix_alert_rule_obj", "alert", ["rule_code", "obj_id"], unique=False)
    op.create_index(op.f("ix_alert_status"), "alert", ["status"], unique=False)
    op.create_index(op.f("ix_alert_triggered_at"), "alert", ["triggered_at"], unique=False)
    op.create_table(
        "action_run",
        sa.Column(
            "action_type_code",
            sa.String(length=64),
            nullable=False,
            comment="动作类型编号（软引用 action_type，FK to add later）",
        ),
        sa.Column("action_name", sa.String(length=64), nullable=False, comment="动作名称（前端 action）"),
        sa.Column("type_version", sa.String(length=16), nullable=True),
        sa.Column(
            "source_type",
            sa.Enum(
                "rule",
                "alert",
                "alert_ops",
                "qa",
                "manual",
                "scenario",
                name="sourcetype",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("source_label", sa.String(length=16), nullable=False, comment="来源原文"),
        sa.Column("rule_code", sa.String(length=64), nullable=True, comment="软引用 rule，FK to add later"),
        sa.Column("rule_version", sa.String(length=16), nullable=True),
        sa.Column("alert_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "qa_session", sa.String(length=64), nullable=True, comment="问数会话（软引用，FK to add later）"
        ),
        sa.Column("qa_session_title", sa.String(length=200), nullable=True),
        sa.Column(
            "scenario_code", sa.String(length=64), nullable=True, comment="软引用 scenario，FK to add later"
        ),
        sa.Column("scenario_name", sa.String(length=200), nullable=True),
        sa.Column("obj_kind", sa.String(length=32), nullable=True),
        sa.Column("obj_id", sa.String(length=64), nullable=True),
        sa.Column("obj_label", sa.String(length=200), nullable=True),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "params_text", sa.Text(), nullable=True, comment="参数摘要（ACTIONS.params / RUNS.summary）"
        ),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("result_code", sa.String(length=200), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("initiator", sa.String(length=64), nullable=False),
        sa.Column("confirmer", sa.String(length=64), nullable=True),
        sa.Column("approver", sa.String(length=64), nullable=True),
        sa.Column("idem_key", sa.String(length=128), nullable=False),
        sa.Column("biz_key", sa.String(length=128), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "staged",
                "approving",
                "executing",
                "succeeded",
                "failed",
                "unknown",
                "withdrawn",
                "reconciled",
                name="actionstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "sys_code",
            sa.String(length=64),
            nullable=True,
            comment="外部系统编号（软引用 ext_system，FK to add later）",
        ),
        sa.Column("ext_ref", sa.String(length=128), nullable=True),
        sa.Column("fail_reason", sa.Text(), nullable=True),
        sa.Column("unknown_reason", sa.Text(), nullable=True),
        sa.Column(
            "reconcile_result",
            sa.Enum(
                "succeeded",
                "not_executed",
                name="reconcileresult",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=True,
        ),
        sa.Column("reconcile_note", sa.Text(), nullable=True),
        sa.Column("reconcile_by", sa.String(length=64), nullable=True),
        sa.Column("withdraw_reason", sa.Text(), nullable=True),
        sa.Column("retry_of_id", sa.BigInteger(), nullable=True),
        sa.Column("retried_as_id", sa.BigInteger(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "in_center",
            sa.Boolean(),
            server_default="true",
            nullable=False,
            comment="[推断] 是否出现在动作中心（ACTIONS 与新建为 true；RUNS 历史为 false）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alert.id"], name=op.f("fk_action_run_alert_id_alert")),
        sa.ForeignKeyConstraint(
            ["retried_as_id"], ["action_run.id"], name=op.f("fk_action_run_retried_as_id_action_run")
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_id"], ["action_run.id"], name=op.f("fk_action_run_retry_of_id_action_run")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_run")),
        sa.UniqueConstraint("code", name=op.f("uq_action_run_code")),
        comment="动作执行记录（动作中心 + 动作类型执行记录）",
    )
    op.create_index(op.f("ix_action_run_action_type_code"), "action_run", ["action_type_code"], unique=False)
    op.create_index(op.f("ix_action_run_alert_id"), "action_run", ["alert_id"], unique=False)
    op.create_index("ix_action_run_center_status", "action_run", ["in_center", "status"], unique=False)
    op.create_index(op.f("ix_action_run_idem_key"), "action_run", ["idem_key"], unique=False)
    op.create_index(op.f("ix_action_run_obj_id"), "action_run", ["obj_id"], unique=False)
    op.create_index(op.f("ix_action_run_rule_code"), "action_run", ["rule_code"], unique=False)
    op.create_index(op.f("ix_action_run_status"), "action_run", ["status"], unique=False)
    op.create_index(op.f("ix_action_run_submitted_at"), "action_run", ["submitted_at"], unique=False)
    op.create_index(
        "uq_action_run_idem_key_nonseed",
        "action_run",
        ["idem_key"],
        unique=True,
        postgresql_where=sa.text("NOT is_seed"),
    )
    op.create_foreign_key(
        op.f("fk_alert_action_run_id_action_run"), "alert", "action_run", ["action_run_id"], ["id"]
    )
    op.create_table(
        "alert_log",
        sa.Column("alert_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False, comment="同一告警内的顺序（1 起）"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False, comment="操作人姓名 / 系统"),
        sa.Column(
            "op",
            sa.Enum(
                "created",
                "claim",
                "transfer",
                "close",
                "start_action",
                "action_result",
                "note",
                name="alertop",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True, comment="AlertStatus code"),
        sa.Column("to_status", sa.String(length=16), nullable=True, comment="AlertStatus code"),
        sa.Column("abnormal", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alert.id"], name=op.f("fk_alert_log_alert_id_alert")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_log")),
        sa.UniqueConstraint("alert_id", "seq", name=op.f("uq_alert_log_alert_id_seq")),
        comment="告警处理日志（只追加）",
    )
    op.create_index(op.f("ix_alert_log_alert_id"), "alert_log", ["alert_id"], unique=False)
    op.create_table(
        "action_run_log",
        sa.Column("action_run_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=True, comment="前端 ActionLog.desc 原文（说明 / 操作人）"),
        sa.Column(
            "level",
            sa.Enum(
                "done",
                "warn",
                "error",
                "unknown",
                name="timelinestatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=True,
            comment="前端 status",
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["action_run_id"], ["action_run.id"], name=op.f("fk_action_run_log_action_run_id_action_run")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_run_log")),
        sa.UniqueConstraint("action_run_id", "seq", name=op.f("uq_action_run_log_action_run_id_seq")),
        comment="动作执行时间线（只追加）",
    )
    op.create_index(
        op.f("ix_action_run_log_action_run_id"), "action_run_log", ["action_run_id"], unique=False
    )
    for table in APPEND_ONLY:
        for sql in enable_append_only(table):
            op.execute(sql)


def downgrade() -> None:
    for table in APPEND_ONLY:
        for sql in disable_append_only(table):
            op.execute(sql)
    op.drop_index(op.f("ix_action_run_log_action_run_id"), table_name="action_run_log")
    op.drop_table("action_run_log")
    op.drop_index(op.f("ix_alert_log_alert_id"), table_name="alert_log")
    op.drop_table("alert_log")
    op.drop_constraint(op.f("fk_alert_action_run_id_action_run"), "alert", type_="foreignkey")
    op.drop_index(
        "uq_action_run_idem_key_nonseed", table_name="action_run", postgresql_where=sa.text("NOT is_seed")
    )
    op.drop_index(op.f("ix_action_run_submitted_at"), table_name="action_run")
    op.drop_index(op.f("ix_action_run_status"), table_name="action_run")
    op.drop_index(op.f("ix_action_run_rule_code"), table_name="action_run")
    op.drop_index(op.f("ix_action_run_obj_id"), table_name="action_run")
    op.drop_index(op.f("ix_action_run_idem_key"), table_name="action_run")
    op.drop_index("ix_action_run_center_status", table_name="action_run")
    op.drop_index(op.f("ix_action_run_alert_id"), table_name="action_run")
    op.drop_index(op.f("ix_action_run_action_type_code"), table_name="action_run")
    op.drop_table("action_run")
    op.drop_index(op.f("ix_alert_triggered_at"), table_name="alert")
    op.drop_index(op.f("ix_alert_status"), table_name="alert")
    op.drop_index("ix_alert_rule_obj", table_name="alert")
    op.drop_index(op.f("ix_alert_rule_code"), table_name="alert")
    op.drop_table("alert")
