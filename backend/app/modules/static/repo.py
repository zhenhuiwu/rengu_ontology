"""static 读取：数据量小（几十到几百行），整表按 seq 读出，筛选 / 分页在 service 里按前端规则做。"""

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, raiseload, selectinload

from app.common.pagination import PageParams
from app.core.errors import NotFound
from app.modules.static.models import (
    Dimension,
    DimMapping,
    DimValue,
    Knowledge,
    LinkType,
    Metric,
    ObjectType,
    ObjectView,
    Property,
    QaPath,
    QaPathExample,
)


class StaticRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def page(self, stmt, params: PageParams, *, focus_id=None, key=None, order=(), scalar=True):
        total = int(
            await self.session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        )
        if focus_id and key is not None:
            ranked = (
                stmt.with_only_columns(
                    key.label("focus_key"), func.row_number().over(order_by=order).label("position")
                )
                .order_by(None)
                .subquery()
            )
            position = await self.session.scalar(
                select(ranked.c.position).where(ranked.c.focus_key == focus_id)
            )
            if position:
                params = PageParams(page=(position - 1) // params.size + 1, size=params.size)
        params = params.clamp(total)
        result = await self.session.execute(stmt.offset(params.offset).limit(params.size))
        return (list(result.scalars()) if scalar else list(result.all())), total, params

    # ---- 对象类型
    async def object_types(self, *, light: bool = False) -> Sequence[ObjectType]:
        stmt = select(ObjectType).order_by(ObjectType.seq, ObjectType.id)
        if light:
            stmt = stmt.options(raiseload("*"))
        return (await self.session.scalars(stmt)).all()

    async def object_type(self, code: str, *, refresh: bool = False) -> ObjectType:
        stmt = select(ObjectType).where(ObjectType.code == code)
        if refresh:
            stmt = stmt.execution_options(populate_existing=True)
        o = await self.session.scalar(stmt)
        if o is None:
            raise NotFound("对象类型不存在", details={"id": code})
        return o

    async def prop_counts(self, ids: list[int] | None = None) -> dict[int, int]:
        stmt = select(Property.object_type_id, func.count()).group_by(Property.object_type_id)
        if ids is not None:
            if not ids:
                return {}
            stmt = stmt.where(Property.object_type_id.in_(ids))
        rows = await self.session.execute(stmt)
        return {k: n for k, n in rows.all()}

    async def views(self, object_type_id: int) -> Sequence[ObjectView]:
        q = select(ObjectView).where(ObjectView.object_type_id == object_type_id).order_by(ObjectView.seq)
        return (await self.session.scalars(q)).all()

    # ---- 关系类型
    async def links(self, *, light: bool = False) -> Sequence[LinkType]:
        stmt = select(LinkType).order_by(LinkType.seq, LinkType.id)
        if light:
            stmt = stmt.options(raiseload("*"))
        return (await self.session.scalars(stmt)).all()

    async def link(self, code: str) -> LinkType:
        lt = await self.session.scalar(select(LinkType).where(LinkType.code == code))
        if lt is None:
            raise NotFound("关系类型不存在", details={"id": code})
        return lt

    # ---- 指标
    async def metrics(self) -> Sequence[Metric]:
        return (await self.session.scalars(select(Metric).order_by(Metric.seq))).all()

    async def stat_metrics(self) -> Sequence[Metric]:
        """METRICS（有统计值的指标），按 METRICS 原顺序。"""
        q = select(Metric).where(Metric.stat_seq.is_not(None)).order_by(Metric.stat_seq)
        return (await self.session.scalars(q)).all()

    async def metric(self, code: str) -> Metric:
        m = await self.session.scalar(select(Metric).where(Metric.code == code))
        if m is None:
            raise NotFound("指标不存在", details={"id": code})
        return m

    # ---- 维度与码表
    async def dimensions(self) -> Sequence[Dimension]:
        return (await self.session.scalars(select(Dimension).order_by(Dimension.seq))).all()

    async def dim_children(
        self, ids: list[int] | None = None
    ) -> tuple[dict[int, list[DimValue]], dict[int, list[DimMapping]]]:
        values: dict[int, list[DimValue]] = defaultdict(list)
        maps: dict[int, list[DimMapping]] = defaultdict(list)
        query = select(DimValue).order_by(DimValue.dimension_id, DimValue.seq, DimValue.id)
        map_query = select(DimMapping).order_by(DimMapping.dimension_id, DimMapping.seq, DimMapping.id)
        if ids is not None:
            query, map_query = (
                query.where(DimValue.dimension_id.in_(ids)),
                map_query.where(DimMapping.dimension_id.in_(ids)),
            )
        for v in await self.session.scalars(query):
            values[v.dimension_id].append(v)
        for m in await self.session.scalars(map_query):
            maps[m.dimension_id].append(m)
        return values, maps

    @staticmethod
    def dimension_query():
        return select(Dimension).options(
            raiseload("*"), joinedload(Dimension.object).raiseload("*"), selectinload(Dimension.levels)
        )

    # ---- 业务知识
    async def knowledge(self) -> Sequence[Knowledge]:
        return (await self.session.scalars(select(Knowledge).order_by(Knowledge.seq))).all()

    # ---- 问数路径
    async def qa_paths(self) -> tuple[Sequence[QaPath], Sequence[QaPathExample]]:
        """路径按插入顺序（组首次出现的顺序）、组内按 seq；试问同。"""
        paths = (await self.session.scalars(select(QaPath).order_by(QaPath.id))).all()
        examples = (await self.session.scalars(select(QaPathExample).order_by(QaPathExample.id))).all()
        return paths, examples


async def pending_change_codes(session: AsyncSession) -> set[str]:
    """公开查询：仍未发布且未驳回的变更单编号，供QA指标目录使用。"""
    from app.modules.static.models import ChangeRequest, CRStatus

    return set(
        await session.scalars(
            select(ChangeRequest.code).where(
                ChangeRequest.status.not_in([CRStatus.published, CRStatus.rejected])
            )
        )
    )


async def change_request_statuses(session: AsyncSession, codes: Sequence[str]) -> dict[str, str]:
    """公开批量状态查询；不存在的编号不出现在结果中。"""
    from app.modules.static.models import ChangeRequest

    if not codes:
        return {}
    rows = await session.execute(
        select(ChangeRequest.code, ChangeRequest.status).where(ChangeRequest.code.in_(codes))
    )
    return {code: status.value for code, status in rows}


async def reserved_target_codes(session: AsyncSession, kind: str) -> list[str]:
    """公开查询：已登记的目标编码（含已发布），供资源编号分配避免复用历史目标。"""
    from app.modules.static.models import CrTarget

    return list(await session.scalars(select(CrTarget.target_id).where(CrTarget.target_kind == kind)))


async def definition_draft_items(session: AsyncSession, cr_code: str, kind: str, code: str) -> list[dict]:
    """公开只读草稿行，保留delete操作；只允许目标仍登记在未发布变更单中。"""
    from app.core.errors import AppError
    from app.modules.static.models import ChangeRequest, CrItem, CRStatus, CrTarget

    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_code))
    if cr is None:
        raise NotFound("变更单不存在")
    if cr.status == CRStatus.published:
        raise AppError("变更单已发布，请使用对应发布版本", code="CR_STATE", status_code=409)
    exists = await session.scalar(
        select(CrTarget.id).where(
            CrTarget.cr_id == cr.id, CrTarget.target_kind == kind, CrTarget.target_id == code
        )
    )
    if exists is None:
        raise NotFound("目标不在该变更单中")
    rows = await session.scalars(
        select(CrItem)
        .where(CrItem.cr_id == cr.id, CrItem.target_kind == kind, CrItem.target_id == code)
        .order_by(CrItem.seq, CrItem.id)
    )
    return [{"section": row.section, "key": row.item_key, "op": row.op, "after": row.after} for row in rows]


async def pending_definition_drafts(
    session: AsyncSession, kind: str, section: str, *, codes: list[str] | None = None, new_only: bool = False
) -> dict[str, dict]:
    """公开读取未发布定义草稿；删除目标/变更单后立即消失，不维护第二份pending状态。"""
    from app.core.schema import _fmt_dt
    from app.modules.static.models import ChangeRequest, CrItem, CRStatus, CrTarget

    if codes == []:
        return {}

    records = await session.execute(
        select(CrItem, ChangeRequest, CrTarget.is_new)
        .join(ChangeRequest, ChangeRequest.id == CrItem.cr_id)
        .join(
            CrTarget,
            (CrTarget.cr_id == CrItem.cr_id)
            & (CrTarget.target_kind == kind)
            & (CrTarget.target_id == CrItem.target_id),
        )
        .where(
            CrItem.target_kind == kind,
            CrItem.section == section,
            *([CrItem.target_id.in_(codes)] if codes is not None else []),
            *([CrTarget.is_new.is_(True)] if new_only else []),
            ChangeRequest.status.in_(
                (CRStatus.draft, CRStatus.in_review, CRStatus.approved, CRStatus.rejected)
            ),
        )
        .order_by(CrItem.seq, CrItem.id)
    )
    result: dict[str, dict] = {}
    for item, cr, is_new in records:
        row = result.setdefault(
            item.target_id,
            {
                "cr": cr.code,
                "crLockVersion": cr.lock_version,
                "status": cr.status.value,
                "isNew": is_new,
                "at": _fmt_dt(item.updated_at),
                "author": cr.owner,
                "note": cr.title,
                "definition": {},
            },
        )
        if item.op != "delete":
            row["definition"][item.item_key] = item.after
    return result
