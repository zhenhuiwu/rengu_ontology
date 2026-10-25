"""Pydantic 输入输出（继承 app.core.schema.ApiModel，字段对齐前端 mock 的 TS interface）。"""

from typing import Literal

from pydantic import Field

from app.core.schema import ApiModel, CodeId, DateTimeSec
from app.modules.admin.models import AuditResult

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
