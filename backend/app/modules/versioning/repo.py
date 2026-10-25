from collections.abc import Sequence

from sqlalchemy import select

from app.common.pagination import PageParams
from app.common.repository import BaseRepository
from app.modules.versioning.models import OntRelease


class ReleaseRepo(BaseRepository[OntRelease]):
    model = OntRelease
    not_found_message = "版本不存在"

    def _ordered(self):
        return select(OntRelease).order_by(OntRelease.published_at.desc(), OntRelease.ver.desc())

    async def latest(self) -> OntRelease | None:
        return await self.session.scalar(self._ordered().limit(1))

    async def page(self, page: PageParams) -> tuple[Sequence[OntRelease], int]:
        return await self.list_page(self._ordered(), page)

    async def add(self, release: OntRelease) -> OntRelease:
        """只追加。不提供 update / delete。"""
        self.session.add(release)
        await self.session.flush()
        return release
