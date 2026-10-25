"""sys_user 加 role、joined_on（admin 模块，前端 Users.vue，api-index §4.1）。

- role：角色名（单个字符串，对齐 mock AdminUser.role；角色表 sys_role 由 PDE 建，届时再改成外键）。
- joined_on：创建日期（前端 createdAt，YYYY-MM-DD）。
- 回填：按 seeds/json/system/ADMIN_USERS.json 的 role、createdAt，按 code 更新已有行
  （之后重跑 seed 也会写同样的值，两边一致）。SYS_USERS 占位人没有 role，保持 null。

Revision ID: admin_0002
Revises: admin_0001
Create Date: 2026-10-26
"""

import json
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

from app.core.config import get_settings

revision: str = "admin_0002"
down_revision: str | None = "admin_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sys_user",
        sa.Column(
            "role",
            sa.String(length=64),
            nullable=True,
            comment="角色名（mock AdminUser.role）；null = 不在用户列表里的占位人（SYS_USERS）",
        ),
    )
    op.add_column(
        "sys_user",
        sa.Column("joined_on", sa.Date(), nullable=True, comment="创建日期（前端 createdAt，YYYY-MM-DD）"),
    )
    path = get_settings().seeds_json_dir / "system" / "ADMIN_USERS.json"
    if path.exists():
        rows = [
            {"code": u["id"], "role": u.get("role"), "joined_on": date.fromisoformat(u["createdAt"])}
            for u in json.loads(path.read_text(encoding="utf-8"))
            if u.get("createdAt")
        ]
        if rows:
            op.get_bind().execute(
                sa.text("UPDATE sys_user SET role = :role, joined_on = :joined_on WHERE code = :code"), rows
            )


def downgrade() -> None:
    op.drop_column("sys_user", "joined_on")
    op.drop_column("sys_user", "role")
