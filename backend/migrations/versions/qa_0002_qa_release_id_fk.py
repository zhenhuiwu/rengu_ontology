"""qa 的 release_id 补成指向 ontology_release(id) 的真外键（CONVENTIONS §6）。

- 涉及 qa_turn、qa_eval_run、qa_object_type_view 三张表（模型改用骨架 OntologyVersionMixin）。
- ON DELETE 用默认的 NO ACTION：版本行永不删除（只追加触发器），外键只保证引用存在；不 CASCADE / SET NULL。
- 列仍可空：种子不填 release_id（种子环境 ontology_release 可能为空），已有数据全是 NULL，加外键不会失败。
- downgrade 删外键、注释改回「软引用」，回到 qa_0001 的样子。
- 接在合并时的 head（event_0001）后面，不紧挨 qa_0001（CONVENTIONS §16 线性单 head）。

Revision ID: qa_0002
Revises: event_0001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "qa_0002"
down_revision: str | None = "event_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("qa_eval_run", "qa_object_type_view", "qa_turn")
FK_COMMENT = "本体版本（ontology_release.id）"
SOFT_COMMENT = "本体版本（ontology_release.id，软引用）"


def _comment(table: str, new: str, old: str) -> None:
    op.alter_column(
        table,
        "release_id",
        existing_type=sa.BIGINT(),
        comment=new,
        existing_comment=old,
        existing_nullable=True,
    )


def upgrade() -> None:
    for t in TABLES:
        _comment(t, FK_COMMENT, SOFT_COMMENT)
        op.create_foreign_key(
            op.f(f"fk_{t}_release_id_ontology_release"), t, "ontology_release", ["release_id"], ["id"]
        )


def downgrade() -> None:
    for t in reversed(TABLES):
        op.drop_constraint(op.f(f"fk_{t}_release_id_ontology_release"), t, type_="foreignkey")
        _comment(t, SOFT_COMMENT, FK_COMMENT)
