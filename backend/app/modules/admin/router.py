"""admin（负责人 PDE工程师）。挂载在 /api/admin/v1 下（app/api.py）。

本分支只实现审计日志页（src/pages/admin/AuditLog.vue，api-index §4.3）：
- GET  /audit-logs          游标分页 CursorResult<AuditRow>，from/to 必填，keyset (time desc, id desc)
- GET  /audit-logs/actions  动作下拉（去重，string[]，不含「全部」）
- POST /audit-logs/export   导出桩，返回 {jobId}，并写一条「导出」审计
用户、角色等页面留到下一阶段。只读（?ver= / ?previewAs=）由全局 ReadonlyGuardDep 拦截写请求。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.common.auth import CurrentUser, require_func
from app.common.pagination import MAX_PAGE_SIZE
from app.core.schema import DateTimeSec
from app.db.session import SessionDep
from app.modules.admin.schemas import (
    AuditCursorOut,
    AuditExportIn,
    AuditExportOut,
    AuditRowOut,
    ResultFilter,
    SearchField,
)
from app.modules.admin.service import AuditLogService, make_filter

router = APIRouter(tags=["admin"])

_audit_view = require_func("audit", "view")


def client_ip(request: Request) -> str:
    """来源 IP：有 X-Forwarded-For 取第一个，否则取连接地址。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


@router.get(
    "/audit-logs",
    response_model=AuditCursorOut,
    summary="审计日志（游标分页，按时间倒序）",
    dependencies=[Depends(_audit_view)],
)
async def list_audit_logs(
    session: SessionDep,
    from_: Annotated[DateTimeSec, Query(alias="from", description="起（含），YYYY-MM-DD HH:mm[:ss]")],
    to: Annotated[DateTimeSec, Query(description="止（含）")],
    action: Annotated[str | None, Query(description="动作；不传 / 全部 = 不限")] = None,
    result: Annotated[ResultFilter | None, Query(description="成功 / 失败 / 拒绝；全部 = 不限")] = None,
    field: Annotated[SearchField, Query(description="kw 搜哪个字段")] = "actor",
    kw: Annotated[str | None, Query(description="包含（区分大小写，先 trim）")] = None,
    cursor: Annotated[str | None, Query(description="上一页返回的 nextCursor；不传 = 第一页")] = None,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
) -> AuditCursorOut:
    f = make_filter(from_, to, action, result, field, kw)
    rows, nxt = await AuditLogService(session).page(f, cursor or None, size)
    return AuditCursorOut(items=[AuditRowOut.model_validate(r) for r in rows], next_cursor=nxt)


@router.get(
    "/audit-logs/actions",
    response_model=list[str],
    summary="审计动作下拉（去重）",
    dependencies=[Depends(_audit_view)],
)
async def list_audit_actions(session: SessionDep) -> list[str]:
    return await AuditLogService(session).actions()


@router.post("/audit-logs/export", response_model=AuditExportOut, summary="导出审计日志（桩：只返回 jobId）")
async def export_audit_logs(
    body: AuditExportIn,
    request: Request,
    session: SessionDep,
    user: Annotated[CurrentUser, Depends(_audit_view)],
) -> AuditExportOut:
    f = make_filter(body.from_, body.to, body.action, body.result, body.field, body.kw)
    job_id = await AuditLogService(session).export(f, user, client_ip(request))
    return AuditExportOut(job_id=job_id)
