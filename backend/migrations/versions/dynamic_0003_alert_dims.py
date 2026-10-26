"""动态本体：告警去零售写死——alert.store_id / store_label 换成通用维度 dims（JSON 数组 [{objKind,id,label}]）。

原有门店维度原样迁成 dims=[{objKind:"store", id, label}]；降级时把 store 维度还原回两列。
rule_hit 同样处理（store_id / store_label → dims）。
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


_DIMS_COMMENT = "通用维度值 [{objKind,id,label}]（来自规则去重键，如所属门店；列按维度生成）"


def _to_dims(table: str, comment: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "dims",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment=comment,
        ),
    )
    op.execute(
        f"UPDATE {table} SET dims = jsonb_build_array(jsonb_build_object("
        "'objKind', 'store', 'id', store_id, 'label', coalesce(store_label, store_id))) "
        "WHERE store_id IS NOT NULL"
    )
    op.alter_column(table, "dims", server_default=None)
    op.drop_column(table, "store_label")
    op.drop_column(table, "store_id")


def _from_dims(table: str) -> None:
    op.add_column(table, sa.Column("store_id", sa.String(length=64), nullable=True))
    op.add_column(table, sa.Column("store_label", sa.String(length=200), nullable=True))
    op.execute(
        f"UPDATE {table} SET store_id = d->>'id', store_label = d->>'label' "
        "FROM (SELECT id AS aid, (SELECT e FROM jsonb_array_elements(dims) e "
        f"WHERE e->>'objKind' = 'store' LIMIT 1) AS d FROM {table}) s "
        f"WHERE {table}.id = s.aid AND s.d IS NOT NULL"
    )
    op.drop_column(table, "dims")


def upgrade() -> None:
    _to_dims("alert", _DIMS_COMMENT)
    _to_dims("rule_hit", "通用维度值 [{objKind,id,label}]（同 alert.dims）")
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
    _from_dims("rule_hit")
    _from_dims("alert")
