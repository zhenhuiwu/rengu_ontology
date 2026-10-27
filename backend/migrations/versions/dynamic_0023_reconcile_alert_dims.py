"""动态本体：纠偏 dynamic_0003 的就地修改（幂等对齐，不改历史迁移）。

dynamic_0003 在已发布后被就地改过（rule_hit 回填前后加了只追加守卫的拆装）。
原版本在 rule_hit 有数据时会直接失败，所以按原版本升级成功的库与新版本结构一致；
本迁移只把任何偏离的库拉回同一终态，不依赖库是走哪个版本过来的：

- alert / rule_hit：dims JSONB NOT NULL、无默认值、注释与 0003 一致；
  若仍残留 store_id / store_label（手工回退或半途失败），按 0003 规则回填进 dims 后删列；
- scenario.template_code 存在且 NOT NULL；
- rule_hit 只追加守卫（trg_rule_hit_append_only / trg_rule_hit_no_truncate）存在。

回填期间临时 NO FORCE RLS（迁移属主非超级用户时也能看到全部空间的行），结束后恢复 FORCE。
降级为空操作：本迁移只对齐终态，没有可撤销的结构。

Revision ID: dynamic_0023
Revises: notify_0022
Create Date: 2026-10-27 16:00:00+08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.ddl import disable_append_only, enable_append_only

revision: str = "dynamic_0023"
down_revision: str | None = "notify_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMENTS = {
    "alert": "通用维度值 [{objKind,id,label}]（来自规则去重键，如所属门店；列按维度生成）",
    "rule_hit": "通用维度值 [{objKind,id,label}]（同 alert.dims）",
}


def _columns(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _reconcile_dims(bind, table: str) -> None:
    cols = _columns(bind, table)
    if "dims" not in cols:
        op.execute(f"ALTER TABLE {table} ADD COLUMN dims jsonb NOT NULL DEFAULT '[]'::jsonb")
    legacy = {"store_id", "store_label"} & cols
    if legacy:
        label = "coalesce(store_label, store_id)" if "store_label" in legacy else "store_id"
        if "store_id" in legacy:
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(
                f"UPDATE {table} SET dims = jsonb_build_array(jsonb_build_object("
                f"'objKind', 'store', 'id', store_id, 'label', {label})) "
                "WHERE store_id IS NOT NULL AND dims = '[]'::jsonb"
            )
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        for col in sorted(legacy):
            op.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    dims = next(c for c in sa.inspect(bind).get_columns(table) if c["name"] == "dims")
    if dims["type"].__class__.__name__ != "JSONB":
        op.execute(f"ALTER TABLE {table} ALTER COLUMN dims TYPE jsonb USING dims::jsonb")
    if dims["nullable"]:
        op.execute(f"UPDATE {table} SET dims = '[]'::jsonb WHERE dims IS NULL")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN dims SET NOT NULL")
    op.execute(f"ALTER TABLE {table} ALTER COLUMN dims DROP DEFAULT")
    op.execute(sa.text(f"COMMENT ON COLUMN {table}.dims IS '{_COMMENTS[table]}'"))


def upgrade() -> None:
    bind = op.get_bind()
    # 回填 rule_hit 前先拆守卫；无论之前是否存在，最后统一重建（幂等）。
    for sql in disable_append_only("rule_hit"):
        op.execute(sql)
    for table in ("alert", "rule_hit"):
        _reconcile_dims(bind, table)
    for sql in enable_append_only("rule_hit"):
        op.execute(sql)
    if "template_code" not in _columns(bind, "scenario"):
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
    """空操作：0023 只把结构对齐到 0003 的终态。"""
