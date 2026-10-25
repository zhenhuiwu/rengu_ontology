"""ORM 模型（继承 app.db.base.Base；表名不加模块前缀，主键 IdMixin / CodeMixin，外键指向 id）。

新增模型要在 app/db/models.py 里 import。

sys_user 目前是**最小占位**（本体小白为 ontology_release.published_by_id 外键和 X-Demo-As 解析先建）：
只有用户编号、姓名、账号、部门、状态、最近登录。
角色、部门表等由 PDE 补。
alert_assignable：可被指派告警的人在动态 PEOPLE 列表里的顺序（1..7），null = 不可指派；
admin 的 seed 不写，由动态的 seed 按姓名回填（所以 seed 里姓名必须唯一，见 admin/seeds CHECKS）。
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LabelEnum
from app.db.base import AppendOnlyTableMixin, Base, CodeMixin, MutableTableMixin
from app.db.types import enum_column


class UserStatus(LabelEnum):
    active = "启用"
    disabled = "停用"
    pending = "待激活"


class SysUser(CodeMixin, MutableTableMixin, Base):
    """用户。code = 前端 userId（U010231），X-Demo-As / RENGU_DEV_USER 传的就是它。"""

    __tablename__ = "sys_user"
    __table_args__ = (
        CheckConstraint("alert_assignable >= 1", name="alert_assignable_positive"),
        {"comment": "用户（最小占位，PDE 补全）"},
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="姓名（不唯一）")
    account: Mapped[str | None] = mapped_column(String(128), unique=True, comment="登录账号（邮箱）")
    dept: Mapped[str | None] = mapped_column(
        String(128), comment="部门（mock 原文，如「华东事业部 / 运营中心」）"
    )
    status: Mapped[UserStatus] = mapped_column(
        enum_column(UserStatus, 16), nullable=False, default=UserStatus.active, server_default="active"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    alert_assignable: Mapped[int | None] = mapped_column(
        SmallInteger,
        comment="告警可指派：动态 PEOPLE 列表里的顺序（1..7），null = 不可指派；由动态 seed 按姓名回填",
    )


class AuditResult(LabelEnum):
    """审计结果（前端 AuditRow.result）：库里存英文 code，接口输出中文。"""

    success = "成功"
    failure = "失败"
    denied = "拒绝"


class AuditLog(CodeMixin, AppendOnlyTableMixin, Base):
    """审计日志（只追加：无 lock_version、无软删除；迁移里挂 append_only 触发器）。

    code = 前端 AuditRow.id（seed 原样：AL1792915715000 / ALCR0；新写入 AL<毫秒><4 位十六进制>）。
    actor 照 mock 存展示串「姓名（用户编号）」，软引用，不做外键。
    游标分页按 (time desc, id desc) keyset，索引 ix_audit_log_time_id。
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_time_id", "time", "id"),
        {"comment": "审计日志（只追加）"},
    )

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="发生时间")
    actor: Mapped[str] = mapped_column(String(256), nullable=False, comment="操作人展示串「姓名（编号）」")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="动作（中文）")
    target: Mapped[str] = mapped_column(String(512), nullable=False, comment="对象")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, comment="来源 IP")
    result: Mapped[AuditResult] = mapped_column(
        enum_column(AuditResult, 16), nullable=False, default=AuditResult.success, server_default="success"
    )
