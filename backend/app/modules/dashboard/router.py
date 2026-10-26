"""首页看板接口（Home.vue）。挂载在 /api/biz/v1（app/api.py MOUNTS）。

GET /dashboard/kpis?ids=gmv,order_cnt,gross_margin,ship_ontime[&cutoff=]
    → {items: [{id,name,unit,value,prev}], dataAsOf}；value/prev 读 metric 表（seed 自 mock METRICS）。
GET /stores/ranking?from=&to=&page=&size=
    → {items: [{rank,id,name,city,cnt,amount}], total, page, size, dataAsOf}（页码越界按前端收拢）。
from / to / cutoff：YYYY-MM-DD HH:mm[:ss]，北京时间；只到分钟时含这一整分钟（同 GET /orders 的 cutoff）。
都需要登录（CurrentUserDep，未登录 401）；订单经 apply_row_filter。
"""

import re
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AfterValidator

from app.common.auth import CurrentUserDep
from app.common.pagination import PageParamsDep
from app.core import clock
from app.core.schema import parse_display_dt
from app.db.session import SessionDep
from app.modules.dashboard import service
from app.modules.dashboard.schemas import KpiMetricOut, KpisOut, SalesDailyOut, StoreRankingPage, StoreRankOut

router = APIRouter(tags=["dashboard"])

DEFAULT_KPI_IDS = "gmv,order_cnt,gross_margin,ship_ontime"  # Home.vue:23
_DT_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?")


def _check_dt(v: str | None) -> str | None:
    if v is None:
        return v
    if not _DT_RE.fullmatch(v):
        raise ValueError("格式应为 YYYY-MM-DD HH:mm 或 YYYY-MM-DD HH:mm:ss")
    try:
        parse_display_dt(v)
    except ValueError:
        raise ValueError("不是有效的日期时间") from None
    return v


def _dt_query(desc: str, alias: str | None = None):
    return Annotated[str | None, AfterValidator(_check_dt), Query(alias=alias, description=desc)]


Cutoff = _dt_query("数据截止（含）：YYYY-MM-DD HH:mm[:ss]，北京时间")
From = _dt_query("下单时间起（含）", alias="from")
To = _dt_query("下单时间止（含；只到分钟时含整分钟）", alias="to")


def _upper(v: str | None) -> tuple[datetime | None, datetime | None]:
    """返回 (until 含, before 不含)。"""
    if v is None:
        return None, None
    at = parse_display_dt(v)
    return (None, at + timedelta(minutes=1)) if len(v) == 16 else (at, None)


def _as_of(v: str | None) -> datetime:
    now = clock.now()
    t = min(parse_display_dt(v), now) if v else now
    return t.replace(second=0, microsecond=0)


@router.get("/dashboard/kpis", response_model=KpisOut, summary="首页 KPI 条（指标当前值和上期值）")
async def dashboard_kpis(
    session: SessionDep,
    user: CurrentUserDep,
    ids: Annotated[str, Query(description="指标编号，逗号分隔，按此顺序返回")] = DEFAULT_KPI_IDS,
    cutoff: Cutoff = None,
) -> KpisOut:
    wanted = [i.strip() for i in ids.split(",") if i.strip()]
    rows = await service.kpi_metrics(session, wanted)
    return KpisOut(items=[KpiMetricOut.model_validate(m) for m in rows], data_as_of=_as_of(cutoff))


@router.get("/stores/ranking", response_model=StoreRankingPage, summary="门店销售额排行（不含税，分页）")
async def stores_ranking(
    session: SessionDep,
    user: CurrentUserDep,
    page: PageParamsDep,
    from_: From = None,
    to: To = None,
) -> StoreRankingPage:
    until, before = _upper(to)
    if to is None:
        until = clock.now()
    rows, total = await service.store_ranking(
        session,
        user,
        created_from=parse_display_dt(from_) if from_ else None,
        created_until=until,
        created_before=before,
        page=page,
    )
    return StoreRankingPage.of([StoreRankOut(**r) for r in rows], total, page, data_as_of=_as_of(to))


@router.get("/dashboard/sales/daily", response_model=SalesDailyOut, summary="按下单日汇总实付折算金额")
async def sales_daily(
    session: SessionDep,
    user: CurrentUserDep,
    cutoff: Cutoff = None,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> SalesDailyOut:
    now = clock.now()
    at = parse_display_dt(cutoff) if cutoff else now
    if cutoff and len(cutoff) == 16:
        at += timedelta(minutes=1, microseconds=-1)
    at = min(at, now)
    rows = await service.sales_daily(session, user, cutoff=at, days=days)
    return SalesDailyOut(items=rows, data_as_of=at)
