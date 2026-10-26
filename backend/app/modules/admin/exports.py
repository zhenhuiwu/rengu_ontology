"""审计导出：持久化成员，按固定集合流 CSV，无队列和临时文件。"""

import csv
import io
from collections.abc import AsyncIterator, Sequence

from sqlalchemy import func, insert, literal, select, tuple_
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.common.auth import CurrentUser
from app.core.errors import NotFound
from app.modules.admin.models import AuditExportJob, AuditExportRow, AuditLog, SysUser
from app.modules.admin.repo import AuditCursor, AuditFilter, AuditLogRepository
from app.modules.admin.schemas import AuditRowOut

BATCH_SIZE = 1000
HEADERS = ("编号", "时间", "操作人", "动作", "对象", "来源 IP", "结果")


async def create_manifest(
    session: AsyncSession, f: AuditFilter, user: CurrentUser, audit: AuditLog
) -> AuditExportJob:
    user_id = await session.scalar(select(SysUser.id).where(SysUser.code == user.id))
    job = AuditExportJob(
        code=f"EXP-{audit.code}",
        user_id=user_id,
        created_by=user.id,
        filters={
            "from": f.start.isoformat(),
            "to": f.end.isoformat(),
            "action": f.action,
            "result": f.result.value if f.result is not None else None,
            "field": f.field,
            "kw": f.kw,
        },
    )
    session.add(job)
    await session.flush()
    # 一条 INSERT SELECT 的语句快照冻结成员；较低 ID 的未提交记录也不会迟到混入。
    members = AuditLogRepository(session)._filtered(f).with_only_columns(literal(job.id), AuditLog.id)
    await session.execute(
        insert(AuditExportRow).from_select(["job_id", "audit_id"], members.where(AuditLog.id != audit.id))
    )
    return job


async def owned_job(session: AsyncSession, code: str, user: CurrentUser) -> AuditExportJob:
    job = await session.scalar(
        select(AuditExportJob)
        .join(SysUser, SysUser.id == AuditExportJob.user_id)
        .where(AuditExportJob.code == code, SysUser.code == user.id)
    )
    if job is None:
        raise NotFound("导出任务不存在")
    return job


async def row_count(session: AsyncSession, job_id: int) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(AuditExportRow).where(AuditExportRow.job_id == job_id)
        )
        or 0
    )


async def read_batch(session: AsyncSession, job_id: int, cursor: AuditCursor | None) -> list[AuditLog]:
    stmt = (
        select(AuditLog)
        .join(AuditExportRow, AuditExportRow.audit_id == AuditLog.id)
        .where(AuditExportRow.job_id == job_id)
    )
    if cursor is not None:
        stmt = stmt.where(tuple_(AuditLog.time, AuditLog.id) < tuple_(cursor.time, cursor.id))
    return list(
        (
            await session.scalars(stmt.order_by(AuditLog.time.desc(), AuditLog.id.desc()).limit(BATCH_SIZE))
        ).all()
    )


def csv_cell(value: str) -> str:
    # 引号只能保护 CSV 语法，不能阻止电子表格把文本当公式执行。
    return (
        "'" + value
        if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))
        else value
    )


def csv_batch(rows: Sequence[AuditLog], *, header: bool = False) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out, quoting=csv.QUOTE_ALL)
    if header:
        out.write("\ufeff")
        writer.writerow(HEADERS)
    for row in rows:
        wire = AuditRowOut.model_validate(row).model_dump(mode="json")
        writer.writerow(
            csv_cell(str(wire[k])) for k in ("id", "time", "actor", "action", "target", "ip", "result")
        )
    return out.getvalue().encode("utf-8")


async def stream_csv(
    engine: AsyncEngine, job_id: int, first: bytes, cursor: AuditCursor | None, more: bool
) -> AsyncIterator[bytes]:
    # 首批由请求 session 预读，scope=function 依赖提交并释放连接后才开始发送。
    yield first
    while more:
        async with AsyncSession(engine) as session:
            rows = await read_batch(session, job_id, cursor)
            data = csv_batch(rows)
            cursor = AuditCursor.of(rows[-1]) if rows else None
            more = len(rows) == BATCH_SIZE
        # 不在客户端消费期间持有数据库连接；异常直接中止响应，不能伪造完整文件。
        if data:
            yield data
