"""动态本体：告警去零售写死——alert.store_id / store_label 换成通用维度 dims（JSON 数组 [{objKind,id,label}]）。

原有门店维度原样迁成 dims=[{objKind:"store", id, label}]；降级时把 store 维度还原回两列。
情景推演模板化：scenario.template_code（推演模板编号，存量方案全部指向演示模板 demo_retail）。

Revision ID: dynamic_0003
Revises: static_0003
Create Date: 2026-10-26 10:00:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "dynamic_0003"
down_revision: str | None = "static_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "alert",
        sa.Column(
            "dims",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="通用维度值 [{objKind,id,label}]（来自规则去重键，如所属门店；列按维度生成）",
        ),
    )
    op.execute(
        "UPDATE alert SET dims = jsonb_build_array(jsonb_build_object("
        "'objKind', 'store', 'id', store_id, 'label', coalesce(store_label, store_id))) "
        "WHERE store_id IS NOT NULL"
    )
    op.alter_column("alert", "dims", server_default=None)
    op.drop_column("alert", "store_label")
    op.drop_column("alert", "store_id")
    op.add_column(
        "scenario",
        sa.Column(
            "template_code",
            sa.String(length=64),
            nullable=False,
            server_default="demo_retail",
            comment="推演模板编号（SC_TEMPLATES.code）",
        ),
    )
    op.alter_column("scenario", "template_code", server_default=None)


def downgrade() -> None:
    op.drop_column("scenario", "template_code")
    op.add_column("alert", sa.Column("store_id", sa.String(length=64), nullable=True))
    op.add_column("alert", sa.Column("store_label", sa.String(length=200), nullable=True))
    op.execute(
        "UPDATE alert SET store_id = d->>'id', store_label = d->>'label' "
        "FROM (SELECT id AS aid, (SELECT e FROM jsonb_array_elements(dims) e "
        "WHERE e->>'objKind' = 'store' LIMIT 1) AS d FROM alert) s "
        "WHERE alert.id = s.aid AND s.d IS NOT NULL"
    )
    op.drop_column("alert", "dims")
