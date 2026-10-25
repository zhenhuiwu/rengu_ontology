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
