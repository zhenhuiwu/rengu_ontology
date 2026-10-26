"""ORM 模型（继承 app.db.base.Base；表名不加模块前缀，主键 IdMixin / CodeMixin，外键指向 id）。

新增模型要在 app/db/models.py 里 import。

sys_user 目前是**最小占位**（本体小白为 ontology_release.published_by_id 外键和 X-Demo-As 解析先建）：
只有用户编号、姓名、账号、部门、状态、最近登录；admin_0002 补了 role（角色名，单个字符串，
对齐 mock AdminUser.role）和 joined_on（前端 createdAt）。角色表、部门表等由 PDE 补。
alert_assignable：可被指派告警的人在动态 PEOPLE 列表里的顺序（1..7），null = 不可指派；
admin 的 seed 不写，由动态的 seed 按姓名回填（所以 seed 里姓名必须唯一，见 admin/seeds CHECKS）。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import LabelEnum
from app.db.base import (
    AppendOnlyTableMixin,
    Base,
    CodeMixin,
    CreatedColumnsMixin,
    IdMixin,
    MutableTableMixin,
    SeedMarkMixin,
)
from app.db.types import JsonType, enum_column


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
    role: Mapped[str | None] = mapped_column(
        String(64), comment="角色名（mock AdminUser.role）；null = 不在用户列表里的占位人（SYS_USERS）"
    )
    joined_on: Mapped[date | None] = mapped_column(Date, comment="创建日期（前端 createdAt，YYYY-MM-DD）")
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
    action: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="动作（中文，如「导出」）"
    )
    target: Mapped[str] = mapped_column(String(512), nullable=False, comment="对象")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, comment="来源 IP")
    result: Mapped[AuditResult] = mapped_column(
        enum_column(AuditResult, 16), nullable=False, default=AuditResult.success, server_default="success"
    )


# ---- 角色与权限（Roles.vue，api-index §4.2；admin_0003）----


class SysRole(CodeMixin, MutableTableMixin, Base):
    """角色。code = 前端 Role.id（R-01）。

    func：页面 key → none|view|manage（mock Role.func，FUNC_PAGES 全量）；
    objs：ObjPerm[]（mock Role.objs，PERM_OBJECTS 顺序）。两者整体读写，存 JSON。
    changed_at / changed_by：前端 updatedAt / updatedBy（人名展示串），只在业务修改时写，seed 照 mock。
    """

    __tablename__ = "sys_role"
    __table_args__ = ({"comment": "角色"},)

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="角色名（唯一）")
    description: Mapped[str] = mapped_column(
        String(512), nullable=False, default="", server_default="", comment="说明"
    )
    built_in: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", comment="内置角色（只读、不能删）"
    )
    default_deny: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", comment="未配范围的对象类型默认不可见"
    )
    func: Mapped[dict] = mapped_column(
        JsonType, nullable=False, comment="功能权限 {pageKey: none|view|manage}"
    )
    objs: Mapped[list] = mapped_column(JsonType, nullable=False, comment="对象与动作权限 ObjPerm[]")
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="前端 updatedAt"
    )
    changed_by: Mapped[str] = mapped_column(String(64), nullable=False, comment="前端 updatedBy（姓名）")


class SysRoleScope(CodeMixin, CreatedColumnsMixin, SeedMarkMixin, Base):
    """角色的行级范围 RowScope{id,objType,prop,op,values}。code = 前端 scope id（S-31）。"""

    __tablename__ = "sys_role_scope"
    __table_args__ = ({"comment": "角色行级范围"},)

    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_role.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="角色内顺序")
    obj_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="对象类型中文名（订单）")
    prop: Mapped[str] = mapped_column(String(64), nullable=False, comment="权限依据属性（大区）")
    op: Mapped[str] = mapped_column(String(4), nullable=False, comment="= 或 ∈")
    values: Mapped[list] = mapped_column(JsonType, nullable=False, comment="取值 string[]")


class SysRoleMember(IdMixin, CreatedColumnsMixin, SeedMarkMixin, Base):
    """角色成员（一个用户可以有多个角色；sys_user.role 只是用户页显示的主角色）。

    seq：列表顺序，升序。seed 照 mock roleMembers() 的顺序 0..n-1；新添加的成员排最前（min-1），
    与 Roles.vue addMembers `[...add, ...members]` 一致。
    """

    __tablename__ = "sys_role_member"
    __table_args__ = (
        UniqueConstraint("role_id", "user_id", name="uq_sys_role_member_role_user"),
        {"comment": "角色成员"},
    )

    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_role.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="列表顺序（升序）")
