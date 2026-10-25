from collections.abc import Sequence

from sqlalchemy import select

from app.common.pagination import PageParams
from app.common.repository import BaseRepository
from app.core.errors import NotFound
from app.modules.versioning.models import OntologyRelease


class ReleaseRepo(BaseRepository[OntologyRelease]):
    model = OntologyRelease
    not_found_message = "版本不存在"

    def _ordered(self):
        return select(OntologyRelease).order_by(
            OntologyRelease.published_at.desc(), OntologyRelease.ver.desc()
        )

    async def by_ver(self, ver: str) -> OntologyRelease | None:
        return await self.session.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))

    async def by_ver_or_404(self, ver: str) -> OntologyRelease:
        r = await self.by_ver(ver)
        if r is None:
            raise NotFound(self.not_found_message, details={"ver": ver})
        return r

    async def all(self) -> Sequence[OntologyRelease]:
        """前端 RELEASES 不分页（版本选择器要全量）。"""
        return (await self.session.scalars(self._ordered())).all()

    async def latest(self) -> OntologyRelease | None:
        return await self.session.scalar(self._ordered().limit(1))

    async def page(self, page: PageParams) -> tuple[Sequence[OntologyRelease], int]:
        return await self.list_page(self._ordered(), page)

    async def add(self, release: OntologyRelease) -> OntologyRelease:
        """只追加。不提供 update / delete。"""
        self.session.add(release)
        await self.session.flush()
        return release
