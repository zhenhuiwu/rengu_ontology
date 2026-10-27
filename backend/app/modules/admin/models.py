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
    Text,
    UniqueConstraint,
    text,
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
    ontology_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ontology_workspace.id"))
    actor: Mapped[str] = mapped_column(String(256), nullable=False, comment="操作人展示串「姓名（编号）」")
    action: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="动作（中文，如「导出」）"
    )
    target: Mapped[str] = mapped_column(String(512), nullable=False, comment="对象")
    ip: Mapped[str] = mapped_column(String(64), nullable=False, comment="来源 IP")
    result: Mapped[AuditResult] = mapped_column(
        enum_column(AuditResult, 16), nullable=False, default=AuditResult.success, server_default="success"
    )
    # admin_0025: machine caller context (API key / service account). Human sessions leave these null.
    principal_kind: Mapped[str | None] = mapped_column(String(16), comment="user / service（API 密钥调用）")
    service_account_code: Mapped[str | None] = mapped_column(String(64), comment="服务账号编号（SA-0001）")
    api_key_prefix: Mapped[str | None] = mapped_column(String(16), comment="调用密钥前缀（不含密钥）")


# ---- 角色与权限（Roles.vue，api-index §4.2；admin_0003）----


class AuditExportJob(CodeMixin, AppendOnlyTableMixin, Base):
    """可下载的固定成员清单；不代表后台已生成 CSV 文件。"""

    __tablename__ = "audit_export_job"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_user.id"), nullable=False)
    filters: Mapped[dict] = mapped_column(JsonType, nullable=False)


class AuditExportRow(IdMixin, Base):
    __tablename__ = "audit_export_row"
    __table_args__ = (UniqueConstraint("job_id", "audit_id"),)

    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("audit_export_job.id"), nullable=False)
    audit_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("audit_log.id"), nullable=False)


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


class SysRoleScope(IdMixin, CreatedColumnsMixin, SeedMarkMixin, Base):
    """角色的行级范围 RowScope{id,objType,prop,op,values}。code = 前端 scope id（S-31）。

    admin_0025：按本体工作空间隔离（ontology_id + FORCE RLS），对象类型用 object_type_id 稳定引用；
    obj_type 只保留展示名。object_type_id 为空且 object_type_code 也为空 = 历史数据，按名称兼容匹配；
    object_type_code 有值但 id 为空 = 引用的对象类型已删除，按无法映射处理（拒绝，不放宽）。
    """

    __tablename__ = "sys_role_scope"
    __table_args__ = (UniqueConstraint("ontology_id", "code"), {"comment": "角色行级范围"})

    ontology_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ontology_workspace.id"),
        nullable=False,
        index=True,
        server_default=text("NULLIF(current_setting('rengu.ontology_id', true), '')::bigint"),
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, comment="业务编号（前端 id）")
    object_type_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("object_type.id", ondelete="SET NULL"), comment="对象类型（稳定引用）"
    )
    object_type_code: Mapped[str | None] = mapped_column(String(64), comment="对象类型编号（写入时快照）")
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sys_role.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="角色内顺序")
    obj_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="对象类型中文名（订单）")
    prop: Mapped[str] = mapped_column(String(64), nullable=False, comment="权限依据属性（大区）")
    op: Mapped[str] = mapped_column(String(4), nullable=False, comment="= 或 ∈")
    values: Mapped[list] = mapped_column(JsonType, nullable=False, comment="取值 string[]")

    @property
    def label(self) -> str:
        """匹配 / 展示用对象名：roles.scopes_of 按 object_type_id 解析成当前显示名，否则用存储名。"""
        return self.__dict__.get("_label", self.obj_type)


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


# ---- 租户与组织（顶栏租户 / 组织切换，api-index §1.11 GET /tenants；admin_0004）----


class SysTenant(CodeMixin, MutableTableMixin, Base):
    """租户（一期单租户，只有一行）。code = 租户 ID（mock SYSTEM_SETTINGS.tenant.tenantId）。

    顶栏显示「简称 / 组织名」，不再写死。
    seed：extra/system/TENANT.json（照 mock settings.ts，零售只是演示样例）。
    """

    __tablename__ = "sys_tenant"
    __table_args__ = ({"comment": "租户"},)

    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="租户全称")
    short_name: Mapped[str] = mapped_column(String(32), nullable=False, comment="简称（顶栏、导出抬头）")
    industry: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default="", comment="行业"
    )


class SysOrg(CodeMixin, MutableTableMixin, Base):
    """顶栏可切换的组织（事业部 / 分公司……任意行业的一级组织）。code = 前端 tenant 选项 value（hd）。

    seed：extra/system/ORGS.json（照 stores/app.ts TENANTS 和 settings.ts defaultOrg）。
    """

    __tablename__ = "sys_org"
    __table_args__ = ({"comment": "组织（顶栏切换）"},)

    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="组织名")
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0", comment="显示顺序"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", comment="默认组织"
    )


class ApiKey(CodeMixin, MutableTableMixin, Base):
    __tablename__ = "api_key"
    __table_args__ = (
        Index("uq_api_key_live_name", "name", unique=True, postgresql_where=text("revoked_at IS NULL")),
        CheckConstraint(
            "(principal_kind = 'user' AND user_id IS NOT NULL AND service_account_id IS NULL) OR "
            "(principal_kind = 'service' AND service_account_id IS NOT NULL AND user_id IS NULL)",
            name="principal",
        ),
    )

    name: Mapped[str] = mapped_column(String(40), nullable=False)
    env: Mapped[str] = mapped_column(String(8), nullable=False)
    # admin_0025: principal_kind user → user_id；service → service_account_id（二选一，CHECK 约束）
    principal_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="user")
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("sys_user.id"))
    service_account_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("service_account.id"))
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    last4: Mapped[str] = mapped_column(String(4), nullable=False)
    scopes: Mapped[list] = mapped_column(JsonType, nullable=False)
    ontology_ids: Mapped[list] = mapped_column(JsonType, nullable=False)
    func_snapshot: Mapped[dict] = mapped_column(JsonType, nullable=False)
    ip_allow: Mapped[list] = mapped_column(JsonType, nullable=False)
    note: Mapped[str] = mapped_column(String(512), nullable=False)
    creator: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(128))
    # null = 不限（该 scope 下全部类型）；列表 = 只允许这些动作/事件类型编号
    action_types: Mapped[list | None] = mapped_column(JsonType)
    event_types: Mapped[list | None] = mapped_column(JsonType)
    function_types: Mapped[list | None] = mapped_column(JsonType)
    rate_per_minute: Mapped[int | None] = mapped_column(Integer)
    quota_per_day: Mapped[int | None] = mapped_column(Integer)


class ServiceAccount(CodeMixin, MutableTableMixin, Base):
    """机器调用方（Palantir 集成等）。

    权限 = 绑定的服务角色（复用 sys_role 的功能/对象/行级范围）∩ 密钥 scope。
    """

    __tablename__ = "service_account"
    __table_args__ = ({"comment": "服务账号"},)

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(512), nullable=False, server_default="")
    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sys_role.id"), nullable=False)
    creator: Mapped[str] = mapped_column(String(128), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(128))


class ApiKeyUsage(IdMixin, Base):
    """限流计数：每个密钥每分钟 / 每天一行，同一事务内原子 +1（随请求事务提交 / 回滚）。"""

    __tablename__ = "api_key_usage"
    __table_args__ = (UniqueConstraint("key_id", "period", "bucket"),)

    key_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("api_key.id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(8), nullable=False, comment="minute / day")
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calls: Mapped[int] = mapped_column(Integer, nullable=False)


class ApiKeyCall(IdMixin, AppendOnlyTableMixin, Base):
    __tablename__ = "api_key_call"
    __table_args__ = (Index("ix_api_key_call_key_id_created_at_id", "key_id", "created_at", "id"),)

    key_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("api_key.id"), nullable=False)
    api: Mapped[str] = mapped_column(String(256), nullable=False)
    ip: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class PlatformSetting(CodeMixin, MutableTableMixin, Base):
    """Shared platform configuration; reversible credentials never leave the server."""

    __tablename__ = "platform_setting"

    value: Mapped[dict] = mapped_column(JsonType, nullable=False)
    secret_enc: Mapped[str | None] = mapped_column(Text)
