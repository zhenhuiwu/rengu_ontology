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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, Float, and_, case, cast, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.pagination import PageParams
from app.common.row_filter import apply_row_filter
from app.core.config import get_settings
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
    page: PageParams,
) -> tuple[list[dict], int]:
    stmt = select(BizOrder.store_id, BizOrder.amount, BizOrder.created_at_biz)
    stmt = await apply_row_filter(stmt, user, "order", ORDER_COLUMNS, ORDER_LINKS)
    visible = stmt.subquery()
    conditions = []
    if created_from is not None:
        conditions.append(visible.c.created_at_biz >= created_from)
    if created_until is not None:
        conditions.append(visible.c.created_at_biz <= created_until)
    if created_before is not None:
        conditions.append(visible.c.created_at_biz < created_before)
    inside = and_(*conditions) if conditions else true()
    grouped = (
        select(
            visible.c.store_id,
            func.sum(case((inside, visible.c.amount), else_=0)).label("amount"),
            func.count().filter(inside).label("cnt"),
        )
        .group_by(visible.c.store_id)
        .subquery()
    )
    total = int(await session.scalar(select(func.count()).select_from(grouped)) or 0)
    p = page.clamp(total)
    # Match the displayed JS rounding before ordering; equal rounded amounts retain store.id order.
    net = func.floor(cast(grouped.c.amount, Float) / (1 + TAX_RATE) * 100 + 0.5) / 100
    statement = (
        select(Store.code, Store.name, Store.city, grouped.c.cnt, grouped.c.amount)
        .join(grouped, grouped.c.store_id == Store.id)
        .order_by(net.desc(), Store.id)
        .limit(p.size)
        .offset(p.offset)
    )
    rows = (await session.execute(statement)).all()
    return [
        {
            "id": row.code,
            "name": row.name,
            "city": row.city,
            "cnt": row.cnt,
            "amount": ex_tax(float(row.amount)),
            "rank": p.offset + i + 1,
        }
        for i, row in enumerate(rows)
    ], total


async def sales_daily(session: AsyncSession, user: object, *, cutoff: datetime, days: int) -> list[dict]:
    """按业务时区下单日汇总实付，复用排行的统一 13% 折算；不是含退款的通用指标求值。"""
    tz = get_settings().display_tz
    last = cutoff.astimezone(ZoneInfo(tz)).date()
    first = last - timedelta(days=days - 1)
    start = datetime.combine(first, datetime.min.time(), tzinfo=ZoneInfo(tz))
    day = cast(func.timezone(tz, BizOrder.created_at_biz), Date)
    stmt = (
        select(day.label("day"), func.sum(BizOrder.amount).label("amount"))
        .where(BizOrder.created_at_biz >= start, BizOrder.created_at_biz <= cutoff)
        .group_by(day)
        .order_by(day)
    )
    stmt = await apply_row_filter(stmt, user, "order", ORDER_COLUMNS, ORDER_LINKS)
    amounts = {r.day: ex_tax(float(r.amount)) for r in (await session.execute(stmt)).all()}
    return [
        {"date": (d := first + timedelta(days=i)).isoformat(), "value": amounts.get(d, 0.0)}
        for i in range(days)
    ]
