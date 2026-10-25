"""业务逻辑。不 commit；跨模块只通过 app/contracts 的 Protocol 调用。

（ext-systems 详情里的「关联动作类型」直接查 action_type：action 和 integration 同属 Palantir专家，
动作类型本来就外键到 ext_system。）
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.action.models import ActionType
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from app.modules.integration.repo import ExtSystemRepo

PROD = "生产"


def is_writeback_target(s: ExtSystem) -> bool:
    """前端 writebackSystems()：生产环境且未停用。"""
    return s.env == PROD and s.status != ExtSystemStatus.disabled


class ExtSystemService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ExtSystemRepo(session)

    async def search(self, *, writeback: bool, kw: str | None) -> list[ExtSystem]:
        """kw 匹配名称 / 编号 / 厂商（前端外部系统页没有搜索框，匹配哪些字段是【推断】）。"""
        rows = list(await self.repo.all_ordered())
        if writeback:
            rows = [s for s in rows if is_writeback_target(s)]
        if kw:
            rows = [s for s in rows if kw in s.name or kw in s.code or kw in s.vendor]
        return rows

    async def get(self, code: str) -> tuple[ExtSystem, Sequence[ActionType]]:
        s = await self.repo.get_by_code_or_404(code)
        stmt = (
            select(ActionType)
            .where(ActionType.sys_id == s.id, ActionType.retired_release_id.is_(None))
            .order_by(ActionType.id)
        )
        return s, (await self.session.scalars(stmt)).all()
