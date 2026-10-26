"""Pydantic 输入输出（继承 app.core.schema.ApiModel，字段对齐前端 mock 的 TS interface）。"""

from typing import Literal

from pydantic import AliasChoices, Field

from app.common.pagination import Page
from app.core.schema import ApiModel, CodeId, DateTimeMin, DateTimeSec, DisplayDate
from app.modules.admin.models import AuditResult, UserStatus

ResultFilter = Literal["全部", "成功", "失败", "拒绝"]
SearchField = Literal["actor", "target"]


class AuditRowOut(ApiModel):
    """前端 AuditRow{id,time,actor,action,target,ip,result}（mock/admin.ts:39）。"""

    id: CodeId
    time: DateTimeSec
    actor: str
    action: str
    target: str
    ip: str
    result: AuditResult


class AuditCursorOut(ApiModel):
    """CursorResult<AuditRow>（src/mock/paginate.ts）：没有 total；nextCursor=null 表示到底。"""

    items: list[AuditRowOut]
    next_cursor: str | None = None


class AuditExportIn(ApiModel):
    """导出：和列表同一套筛选条件（JSON body；from/to 必填）。"""

    from_: DateTimeSec = Field(alias="from")
    to: DateTimeSec
    action: str | None = None
    result: ResultFilter | None = None
    field: SearchField = "actor"
    kw: str | None = None


class AuditExportOut(ApiModel):
    job_id: str


# ---- 用户（Users.vue，api-index §4.1）----

StatusFilter = Literal["全部", "启用", "停用", "待激活"]
Email = Field(pattern=r"^\S+@\S+$", max_length=128)  # 前端 formOk：/^\S+@\S+$/


class AdminUserOut(ApiModel):
    """前端 AdminUser & {pendingApprovals}（mock/admin.ts:9）。"""

    id: CodeId
    name: str
    account: str | None
    dept: str | None
    role: str | None
    status: UserStatus
    last_login: DateTimeMin | None = Field(None, validation_alias=AliasChoices("last_login_at", "lastLogin"))
    created_at: DisplayDate | None = Field(None, validation_alias=AliasChoices("joined_on", "createdAt"))
    pending_approvals: int = 0


class AdminUserPage(Page[AdminUserOut]):
    """PageResult<AdminUser & {pendingApprovals}>：items、total（多出的 page、size 前端不用）。"""


class UserUpdateIn(ApiModel):
    name: str = Field(min_length=1, max_length=64)
    dept: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=64)
    status: UserStatus


class UserCreateIn(UserUpdateIn):
    account: str = Email
    status: UserStatus = UserStatus.pending


class ImportRowIn(ApiModel):
    """导入行（模板列：姓名,账号,部门,角色）。全部可空，校验结果放 error 里，不报 422。"""

    name: str = ""
    account: str = ""
    dept: str = ""
    role: str = ""


class ImportPreviewIn(ApiModel):
    """二选一：rows（前端已解析好的行）或 csv（模板 CSV 原文，UTF-8，可带 BOM，首行表头）。"""

    rows: list[ImportRowIn] | None = None
    csv: str | None = Field(None, max_length=1_000_000)


class ImportRowOut(ImportRowIn):
    error: str | None = None


class ImportPreviewOut(ApiModel):
    rows: list[ImportRowOut]


class ImportIn(ApiModel):
    rows: list[ImportRowIn] = Field(max_length=500)


class ImportOut(ApiModel):
    imported: int


class DisableIn(ApiModel):
    transfer_to: str | None = None


# ---- 角色与权限（Roles.vue，api-index §4.2）----

FuncLevel = Literal["none", "view", "manage"]
ScopeOp = Literal["=", "∈"]


class ObjPermIo(ApiModel):
    """mock/roles.ts ObjPerm{objType,api,view,export,actions}。"""

    obj_type: str
    api: str
    view: bool
    export: bool
    actions: list[str] = Field(default_factory=list)


class RowScopeOut(ApiModel):
    """RowScope{id,objType,prop,op,values}。"""

    id: CodeId
    obj_type: str
    prop: str
    op: ScopeOp
    values: list[str]


class RowScopeIn(ApiModel):
    """新增范围：不含 id；op 不传时按取值个数推（一个 =，多个 ∈），与 Roles.vue addScope 一致。"""

    obj_type: str = Field(min_length=1, max_length=64)
    prop: str = Field(min_length=1, max_length=64)
    op: ScopeOp | None = None
    values: list[str] = Field(min_length=1, max_length=200)


class RoleOut(ApiModel):
    """Role（mock/roles.ts）+ memberCount。"""

    id: CodeId
    name: str
    desc: str = Field(validation_alias=AliasChoices("description", "desc"))
    built_in: bool
    updated_at: DateTimeMin = Field(validation_alias=AliasChoices("changed_at", "updatedAt"))
    updated_by: str = Field(validation_alias=AliasChoices("changed_by", "updatedBy"))
    func: dict[str, FuncLevel]
    objs: list[ObjPermIo]
    scopes: list[RowScopeOut] = Field(default_factory=list)
    default_deny: bool = False
    member_count: int = 0
    lock_version: int = 1


class FuncPageOut(ApiModel):
    area: str
    area_key: str
    module: str
    key: str
    title: str


class PermObjectOut(ApiModel):
    name: str
    api: str
    actions: list[str]


class ScopePropOut(ApiModel):
    obj_type: str
    prop: str
    values: list[str]


class RolesMetaOut(ApiModel):
    """页面常量：FUNC_PAGES、PERM_OBJECTS、SCOPE_PROPS；objActions = {对象类型名: 动作[]}。"""

    func_pages: list[FuncPageOut]
    perm_objects: list[PermObjectOut]
    scope_props: list[ScopePropOut]
    obj_actions: dict[str, list[str]]


class RoleCreateIn(ApiModel):
    """新建：copyFrom = 复制某角色（权限、范围照抄，没有成员）；perms = 新建抽屉的权限模板 NR_PERMS。"""

    name: str = Field(min_length=1, max_length=20)
    desc: str = Field("", max_length=512)
    perms: list[str] = Field(default_factory=list)
    copy_from: str | None = None


class RoleUpdateIn(ApiModel):
    name: str = Field(min_length=1, max_length=20)
    desc: str = Field("", max_length=512)
    lock_version: int | None = None


class RolePermissionsIn(ApiModel):
    func: dict[str, FuncLevel]
    objs: list[ObjPermIo]
    lock_version: int | None = None


class MembersAddIn(ApiModel):
    user_ids: list[str] = Field(min_length=1, max_length=500)


class MembersAddOut(ApiModel):
    added: int


class RoleMemberPage(Page[AdminUserOut]):
    """PageResult<AdminUser>。"""


class EffectiveFuncOut(FuncPageOut):
    lv: FuncLevel


class EffectiveObjOut(ApiModel):
    name: str
    scope: str
    actions: list[str]
    exp: bool


class EffectiveAccessOut(ApiModel):
    """mock/roles.ts EffectiveAccess{rs,func,objs,areasIn}：用户所有角色合并后的有效权限。"""

    rs: list[RoleOut]
    func: list[EffectiveFuncOut]
    objs: list[EffectiveObjOut]
    areas_in: list[str]


# ---- 当前用户行级范围 / 租户与组织 ----


class MyScopeCondOut(ApiModel):
    prop: str
    op: str
    values: list[str]
    text: str


class MyObjScopeOut(ApiModel):
    """某对象类型上当前用户能看到的行：unrestricted=不限；否则 conditions 之间是「或」。"""

    obj_type: str
    visible: bool
    unrestricted: bool
    conditions: list[MyScopeCondOut]
    text: str


class MeRowScopeOut(ApiModel):
    user_id: str
    user_name: str
    items: list[MyObjScopeOut]


class TenantOptionOut(ApiModel):
    """顶栏租户 / 组织下拉（api-index GET /tenants）：value = 组织编号，label = 「租户简称 / 组织名」。"""

    value: str
    label: str
    tenant_id: str
    tenant_name: str
    org_name: str
    default: bool
