"""sys_user.alert_assignable：告警可指派的人在动态 PEOPLE 列表里的顺序（1..7），null = 不可指派

admin 的 seed 不写这一列，由动态的 seed 按姓名回填（seed 姓名唯一由 admin CHECKS 保证）。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sys_user",
        sa.Column(
            "alert_assignable",
            sa.SmallInteger(),
            nullable=True,
            comment="告警可指派：动态 PEOPLE 列表里的顺序（1..7），null = 不可指派；由动态 seed 按姓名回填",
        ),
    )
    op.create_check_constraint(
        op.f("ck_sys_user_alert_assignable_positive"), "sys_user", "alert_assignable >= 1"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_sys_user_alert_assignable_positive"), "sys_user", type_="check")
    op.drop_column("sys_user", "alert_assignable")
