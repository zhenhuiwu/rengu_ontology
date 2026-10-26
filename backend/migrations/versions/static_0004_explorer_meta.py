"""通用对象浏览元数据：property 显示标记 + metric 口径变体（generic-object-explorer.md §4、§5）。

只加列，全部带默认值，已有行不用回填；演示类型的初始值由 static seed 写（seeds/explorer_meta.json）。

Revision ID: static_0004
Revises: qa_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "static_0004"
down_revision: str | None = "qa_0003"  # 链：qa_0003 → static_0004 → admin_0003 → admin_0004 → static_0003（PDE 合并定）
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAGS = ("searchable", "filterable", "sortable", "list_visible")


def upgrade() -> None:
    op.add_column(
        "property",
        sa.Column(
            "visibility",
            sa.String(length=16),
            server_default="normal",
            nullable=False,
            comment="prominent/normal/hidden",
        ),
    )
    for f in FLAGS:
        op.add_column("property", sa.Column(f, sa.Boolean(), server_default="false", nullable=False))
    op.add_column(
        "property", sa.Column("list_width", sa.Integer(), nullable=True, comment="列表列宽 px（可选）")
    )
    op.add_column(
        "property",
        sa.Column("format", sa.String(length=32), nullable=True, comment="值格式 money:1/pct/int/raw"),
    )
    op.add_column("property", sa.Column("unit", sa.String(length=16), nullable=True, comment="单位（元/㎡）"))
    op.add_column(
        "property",
        sa.Column(
            "backing_column",
            sa.String(length=64),
            nullable=True,
            comment="分面取值用：受控业务表（sql_guard 登记）里的物理列名",
        ),
    )
    op.add_column(
        "metric",
        sa.Column(
            "variants",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
            comment="口径变体 [{code,label,api,version,default}]",
        ),
    )


def downgrade() -> None:
    op.drop_column("metric", "variants")
    for c in ("backing_column", "unit", "format", "list_width", *reversed(FLAGS), "visibility"):
        op.drop_column("property", c)
