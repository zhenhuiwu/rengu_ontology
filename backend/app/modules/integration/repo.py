"""数据访问（继承 app.common.repository.BaseRepository）。"""

from collections.abc import Sequence

from sqlalchemy import select

from app.common.repository import BaseRepository
from app.modules.integration.models import ExtSystem


class ExtSystemRepo(BaseRepository[ExtSystem]):
    model = ExtSystem
    not_found_message = "外部系统不存在"

    async def all_ordered(self) -> Sequence[ExtSystem]:
        return (await self.session.scalars(select(ExtSystem).order_by(ExtSystem.id))).all()
