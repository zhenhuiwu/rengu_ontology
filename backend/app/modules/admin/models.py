"""ORM 模型（继承 app.db.base.Base；表名不加模块前缀，主键 IdMixin / CodeMixin，外键指向 id）。

新增模型要在 app/db/models.py 里 import。

sys_user 目前是**最小占位**（本体小白为 ontology_release.published_by_id 外键和 X-Demo-As 解析先建）：
只有用户编号、姓名、账号、部门、状态、最近登录。
角色、部门表、alert_assignable / sort_order（PEOPLE）等由 PDE 补。
"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LabelEnum
from app.db.base import Base, CodeMixin, MutableTableMixin
from app.db.types import enum_column


class UserStatus(LabelEnum):
    active = "启用"
    disabled = "停用"
    pending = "待激活"


class SysUser(CodeMixin, MutableTableMixin, Base):
    """用户。code = 前端 userId（U010231），X-Demo-As / RENGU_DEV_USER 传的就是它。"""

    __tablename__ = "sys_user"
    __table_args__ = ({"comment": "用户（最小占位，PDE 补全）"},)

    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="姓名（不唯一）")
    account: Mapped[str | None] = mapped_column(String(128), unique=True, comment="登录账号（邮箱）")
    dept: Mapped[str | None] = mapped_column(
        String(128), comment="部门（mock 原文，如「华东事业部 / 运营中心」）"
    )
    status: Mapped[UserStatus] = mapped_column(
        enum_column(UserStatus, 16), nullable=False, default=UserStatus.active, server_default="active"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
