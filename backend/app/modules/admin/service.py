"""业务逻辑。不 commit；跨模块只通过 app/contracts 的 Protocol 调用。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.core.config import get_settings
from app.core.schema import DATETIME_SEC_FORMAT
from app.modules.admin.models import AuditLog, AuditResult
from app.modules.admin.repo import AuditFilter, AuditLogRepository, insert_audit


def make_filter(
    start: datetime,
    end: datetime,
    action: str | None,
    result: str | None,
    field: str,
    kw: str | None,
) -> AuditFilter:
    """「全部」/ 空串 = 不限（前端下拉的默认值就是「全部」）；kw 先 trim（前端 kw.value.trim()）。"""
    k = (kw or "").strip()
    return AuditFilter(
        start=start,
        end=end,
        action=None if action in (None, "", "全部") else action,
        result=None if result in (None, "", "全部") else AuditResult(result),
        field=field,
        kw=k or None,
    )


def actor_of(user: CurrentUser) -> str:
    """mock 的 actor 展示串：「姓名（用户编号）」。"""
    return f"{user.name}（{user.id}）"


class AuditLogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AuditLogRepository(session)

    async def page(self, f: AuditFilter, cursor: str | None, size: int) -> tuple[list[AuditLog], str | None]:
        return await self.repo.list_cursor(f, cursor, size)

    async def actions(self) -> list[str]:
        return await self.repo.distinct_actions()

    async def export(self, f: AuditFilter, user: CurrentUser, ip: str) -> str:
        """桩：不真正生成文件，只返回 jobId，并按 api-index §4.3「导出本身也写一条审计」。"""
        fmt = DATETIME_SEC_FORMAT
        tz = ZoneInfo(get_settings().display_tz)
        span = f"{f.start.astimezone(tz).strftime(fmt)} 至 {f.end.astimezone(tz).strftime(fmt)}"
        row = await insert_audit(
            self.session,
            actor=actor_of(user),
            action="导出",
            target=f"审计日志（{span}）",
            ip=ip,
            created_by=user.id,
        )
        return f"EXP-{row.code}"
