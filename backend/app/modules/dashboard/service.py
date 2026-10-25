"""首页看板取数。

门店排行口径对齐 Home.vue:77-81：
- 范围：订单 createdAt 在 [from, to] 内（前端 CUT_ORDERS：createdAt <= '2026-10-24 23:00:59'）；
- 门店集合：前端用核心 12 家 STORES（订单只在这 12 家里生成）。库里 store 表是全量 95 家（ALL_STORES），
  [推断] 这里取「有过订单的门店」（不看时间范围），即区间内 0 单的核心门店也会以 0 出现，和前端一致；
- 销售额（不含税）= exTax(Σ实付) = Math.round(Σ/1.13*100)/100（mock/metrics.ts:12），按 JS 浮点算法复刻；
- 排序：销售额降序，同额按门店 seed 顺序（store.id）—— 对应 JS 稳定排序保留 STORES 顺序。
"""

import math
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.row_filter import apply_row_filter
from app.modules.biz.governance import ORDER_COLUMNS, ORDER_LINKS
from app.modules.biz.models import BizOrder, Store
from app.modules.static.models import Metric

TAX_RATE = 0.13


def ex_tax(incl: float) -> float:
    """mock/metrics.ts:12 exTax：Math.round((incl / 1.13) * 100) / 100（JS Math.round = floor(x+0.5)）。"""
    return math.floor((incl / (1 + TAX_RATE)) * 100 + 0.5) / 100


async def kpi_metrics(session: AsyncSession, ids: Sequence[str]) -> list[Metric]:
    rows = (await session.scalars(select(Metric).where(Metric.code.in_(list(ids))))).all()
    by_code = {m.code: m for m in rows}
    return [by_code[i] for i in ids if i in by_code]


async def store_ranking(
    session: AsyncSession,
    user: object,
    *,
    created_from: datetime | None,
    created_until: datetime | None,
    created_before: datetime | None,
) -> list[dict]:
    stmt = select(BizOrder.store_id, BizOrder.amount, BizOrder.created_at_biz)
    stmt = await apply_row_filter(stmt, user, "order", ORDER_COLUMNS, ORDER_LINKS)
    orders = (await session.execute(stmt)).all()
    store_ids = {o.store_id for o in orders}
    if not store_ids:
        return []
    stores = (await session.scalars(select(Store).where(Store.id.in_(store_ids)).order_by(Store.id))).all()
    sums: dict[int, Decimal] = {sid: Decimal(0) for sid in store_ids}
    cnts: dict[int, int] = {sid: 0 for sid in store_ids}
    for o in orders:
        t = o.created_at_biz
        if created_from is not None and t < created_from:
            continue
        if created_until is not None and t > created_until:
            continue
        if created_before is not None and t >= created_before:
            continue
        sums[o.store_id] += o.amount
        cnts[o.store_id] += 1
    rows = [
        {"id": s.code, "name": s.name, "city": s.city, "cnt": cnts[s.id], "amount": ex_tax(float(sums[s.id]))}
        for s in stores
    ]
    rows.sort(key=lambda r: -r["amount"])  # Python 排序稳定：同额保持 store.id 顺序
    return [{**r, "rank": i + 1} for i, r in enumerate(rows)]
