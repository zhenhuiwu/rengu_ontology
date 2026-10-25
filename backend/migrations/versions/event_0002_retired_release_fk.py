"""action_type / event_type 的 retired_release_id 补成指向 ontology_release(id) 的真外键（CONVENTIONS §6）。

- ON DELETE 用默认的 NO ACTION：版本行永不删除（只追加触发器），外键只保证引用存在；不 CASCADE / SET NULL。
- 列仍可空（NULL = 在用）；seed 不写 retired_release_id，已有数据要么 NULL、要么来自 restore_sections
  传入的真实版本 id，加外键不会失败。
- downgrade 删外键、注释改回「软引用」，回到 event_0001 的样子。
- 接在合并时的 head（dynamic_0001）后面（CONVENTIONS §16 线性单 head）。

Revision ID: event_0002
Revises: dynamic_0001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "event_0002"
down_revision: str | None = "dynamic_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("action_type", "event_type")
COL = "retired_release_id"
FK_COMMENT = "整版回滚下线于哪一版（ontology_release.id）"
SOFT_COMMENT = "整版回滚下线于哪一版（ontology_release.id，软引用）"


def _comment(table: str, new: str, old: str) -> None:
    op.alter_column(
        table, COL, existing_type=sa.BIGINT(), comment=new, existing_comment=old, existing_nullable=True
    )


def upgrade() -> None:
    for t in TABLES:
        _comment(t, FK_COMMENT, SOFT_COMMENT)
        op.create_foreign_key(op.f(f"fk_{t}_{COL}_ontology_release"), t, "ontology_release", [COL], ["id"])


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_constraint(op.f(f"fk_{t}_{COL}_ontology_release"), t, type_="foreignkey")
        _comment(t, SOFT_COMMENT, FK_COMMENT)
