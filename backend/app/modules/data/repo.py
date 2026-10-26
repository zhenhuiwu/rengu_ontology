"""数据访问（继承 app.common.repository.BaseRepository）。"""

from collections.abc import Sequence

from sqlalchemy import Select, case, func, or_, select

from app.common.pagination import PageParams
from app.common.repository import BaseRepository
from app.modules.admin.repo import lock_code_seq
from app.modules.data.models import Datasource, DatasourceRun, DatasourceTable, DsType


def _like(kw: str) -> str:
    return "%" + kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class DatasourceRepo(BaseRepository[Datasource]):
    model = Datasource
    not_found_message = "数据连接不存在"

    def search_query(self, *, type_: DsType | None, kw: str | None) -> Select[tuple[Datasource]]:
        """前端 Connections.vue filtered：类型相等；kw = 名称 / 主机包含（区分大小写）或库名包含（不区分）。

        顺序 = 前端 list：新建的在最前（unshift，后建的更前），seed 的按编号。
        """
        stmt = self.base_query()
        if type_ is not None:
            stmt = stmt.where(Datasource.type == type_)
        if kw:
            pat = _like(kw)
            stmt = stmt.where(
                or_(
                    Datasource.name.like(pat, escape="\\"),
                    Datasource.host.like(pat, escape="\\"),
                    Datasource.database.ilike(pat, escape="\\"),
                )
            )
        return stmt.order_by(
            Datasource.is_seed.asc(),
            case((Datasource.is_seed.is_(True), Datasource.code)).asc(),
            Datasource.id.desc(),
        )

    async def max_code_no(self) -> int:
        """DS-xxx 编号的最大数字。先加锁（事务内持有到提交），并发新建不撞号。"""
        await lock_code_seq(self.session, "datasource")
        codes = (
            await self.session.scalars(select(Datasource.code).where(Datasource.code.like("DS-%")))
        ).all()
        nums = [int(c[3:]) for c in codes if c[3:].isdigit()]
        return max(nums, default=0)

    async def add_run(self, run: DatasourceRun) -> None:
        self.session.add(run)


class DatasourceTableRepo(BaseRepository[DatasourceTable]):
    model = DatasourceTable

    async def page_for(
        self, datasource_id: int, kw: str | None, page: PageParams
    ) -> tuple[Sequence[DatasourceTable], int]:
        """前端 tables：表名包含（不区分大小写）或说明包含（区分）；顺序 = mock dsTables（seq）。"""
        stmt = self.base_query().where(DatasourceTable.datasource_id == datasource_id)
        if kw:
            pat = _like(kw)
            stmt = stmt.where(
                or_(
                    func.lower(DatasourceTable.name).like(_like(kw.lower()), escape="\\"),
                    DatasourceTable.comment.like(pat, escape="\\"),
                )
            )
        return await self.list_page(stmt.order_by(DatasourceTable.seq, DatasourceTable.id), page)

    async def all_for(self, datasource_id: int) -> Sequence[DatasourceTable]:
        stmt = self.base_query().where(DatasourceTable.datasource_id == datasource_id)
        return (await self.session.scalars(stmt.order_by(DatasourceTable.seq, DatasourceTable.id))).all()
