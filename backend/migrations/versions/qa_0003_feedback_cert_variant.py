"""qa_feedback 记下 T16「转为认证问答」加上的同问法，T17 重新打开时撤销它。

- 新增可空列 qa_feedback.cert_variant_id → qa_cert_variant(id)，ON DELETE SET NULL
  （同问法被 T8 删掉后不挡删除）。
- 已有数据全是 NULL：历史上 to-cert 加的同问法不追溯（重开时不删）[推断]。
- 接在当前 head（action_0002）后面（CONVENTIONS §16 线性单 head）。

Revision ID: qa_0003
Revises: action_0002
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "qa_0003"
down_revision: str | None = "action_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COMMENT = "T16 转认证问答时加上的同问法（T17 重开时撤销）"


def upgrade() -> None:
    op.add_column(
        "qa_feedback", sa.Column("cert_variant_id", sa.BigInteger(), nullable=True, comment=COMMENT)
    )
    op.create_foreign_key(
        op.f("fk_qa_feedback_cert_variant_id_qa_cert_variant"),
        "qa_feedback",
        "qa_cert_variant",
        ["cert_variant_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_qa_feedback_cert_variant_id_qa_cert_variant"), "qa_feedback", type_="foreignkey"
    )
    op.drop_column("qa_feedback", "cert_variant_id")
