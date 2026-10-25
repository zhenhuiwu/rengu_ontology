"""static 读取：数据量小（几十到几百行），整表按 seq 读出，筛选 / 分页在 service 里按前端规则做。"""

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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

    # ---- 对象类型
    async def object_types(self) -> Sequence[ObjectType]:
        return (await self.session.scalars(select(ObjectType).order_by(ObjectType.seq))).all()

    async def object_type(self, code: str) -> ObjectType:
        o = await self.session.scalar(select(ObjectType).where(ObjectType.code == code))
        if o is None:
            raise NotFound("对象类型不存在", details={"id": code})
        return o

    async def prop_counts(self) -> dict[int, int]:
        rows = await self.session.execute(
            select(Property.object_type_id, func.count()).group_by(Property.object_type_id)
        )
        return {k: n for k, n in rows.all()}

    async def views(self, object_type_id: int) -> Sequence[ObjectView]:
        q = select(ObjectView).where(ObjectView.object_type_id == object_type_id).order_by(ObjectView.seq)
        return (await self.session.scalars(q)).all()

    # ---- 关系类型
    async def links(self) -> Sequence[LinkType]:
        return (await self.session.scalars(select(LinkType).order_by(LinkType.seq))).all()

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

    async def dim_children(self) -> tuple[dict[int, list[DimValue]], dict[int, list[DimMapping]]]:
        values: dict[int, list[DimValue]] = defaultdict(list)
        maps: dict[int, list[DimMapping]] = defaultdict(list)
        for v in await self.session.scalars(select(DimValue).order_by(DimValue.dimension_id, DimValue.seq)):
            values[v.dimension_id].append(v)
        for m in await self.session.scalars(
            select(DimMapping).order_by(DimMapping.dimension_id, DimMapping.seq)
        ):
            maps[m.dimension_id].append(m)
        return values, maps

    # ---- 业务知识
    async def knowledge(self) -> Sequence[Knowledge]:
        return (await self.session.scalars(select(Knowledge).order_by(Knowledge.seq))).all()

    # ---- 问数路径
    async def qa_paths(self) -> tuple[Sequence[QaPath], Sequence[QaPathExample]]:
        """路径按插入顺序（组首次出现的顺序）、组内按 seq；试问同。"""
        paths = (await self.session.scalars(select(QaPath).order_by(QaPath.id))).all()
        examples = (await self.session.scalars(select(QaPathExample).order_by(QaPathExample.id))).all()
        return paths, examples
