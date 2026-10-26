"""qa_session.caliber 改成自由文本：指标口径变体标签（静态 metrics[].variants[].label）。

- 去掉枚举 CHECK（ck_qa_session_sessioncaliber），varchar(8) → varchar(32)，可空（空 = 不限），去掉默认值 ex。
- 旧值换成标签：ex → 不含税、in → 含税。
- 降级：不含税 / 空 → ex，含税 → in，其他标签按 ex [推断]，恢复 CHECK 和默认值。

Revision ID: qa_0004
Revises: static_0003
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "qa_0004"
down_revision: str | None = "static_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CK = "ck_qa_session_sessioncaliber"
NEW = "会话口径：指标口径变体标签（如 不含税 / 含税），自由文本，空 = 不限（qa_0004）"
OLD = "会话口径 ex/in（接口 不含税/含税）"


def upgrade() -> None:
    op.drop_constraint(op.f(CK), "qa_session", type_="check")
    op.alter_column(
        "qa_session",
        "caliber",
        type_=sa.String(32),
        existing_type=sa.String(8),
        nullable=True,
        server_default=None,
        comment=NEW,
        existing_comment=OLD,
    )
    op.execute(
        "UPDATE qa_session SET caliber = CASE caliber WHEN 'in' THEN '含税' ELSE '不含税' END "
        "WHERE caliber IN ('ex', 'in')"
    )


def downgrade() -> None:
    op.execute("UPDATE qa_session SET caliber = CASE WHEN caliber = '含税' THEN 'in' ELSE 'ex' END")
    op.alter_column(
        "qa_session",
        "caliber",
        type_=sa.String(8),
        existing_type=sa.String(32),
        nullable=False,
        server_default="ex",
        comment=OLD,
        existing_comment=NEW,
    )
    op.create_check_constraint(op.f(CK), "qa_session", "caliber IN ('ex', 'in')")
