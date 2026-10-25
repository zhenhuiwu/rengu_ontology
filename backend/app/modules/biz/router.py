"""biz 只读接口（演示业务数据）。挂载在 /api/biz/v1 [推断] 下。

查询统一经 apply_row_filter 注入行级过滤（对象类型 order，一跳 order_belongs_to_store）。

GET /orders 的筛选对齐首页 Home.vue:32-46：
- status / channel：前端选「全部」时客户端**不传**该参数（后台不认“全部”这个值）；
- kw：订单号或门店名包含（前端 `o.id.includes(kw) || storeName.includes(kw)`；
  区分大小写，和前端一致，前端为准；`%`、`_`、`\\` 按字面）；
- cutoff：createdAt <= cutoff（前端 CUTOFF='2026-10-24 23:00:59'，Home.vue:37-39）。收 'YYYY-MM-DD HH:mm'
  或 'YYYY-MM-DD HH:mm:ss'，不带时区按北京时间；只到分钟时含这一整分钟（23:00 ≡ 23:00:59）。
  格式不对 → 422 VALIDATION_FAILED，details.fields=[{field: "cutoff", ...}]。
- 响应多一个 dataAsOf（见 OrdersPage）。
"""

import re
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import AfterValidator

from app.common.auth import CurrentUserDep
from app.common.pagination import PageParamsDep
from app.common.row_filter import apply_row_filter
from app.core import clock
from app.core.errors import NotFound
from app.core.schema import parse_display_dt
from app.db.session import SessionDep
from app.modules.biz.governance import ORDER_COLUMNS, ORDER_LINKS
from app.modules.biz.models import OrderStatus
from app.modules.biz.repo import OrderRepo
from app.modules.biz.schemas import OrderDetailOut, OrderOut, OrdersPage

router = APIRouter(tags=["biz"])

_CUTOFF_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?")


def _check_cutoff(v: str | None) -> str | None:
    if v is None:
        return v
    if not _CUTOFF_RE.fullmatch(v):
        raise ValueError("格式应为 YYYY-MM-DD HH:mm 或 YYYY-MM-DD HH:mm:ss")
    try:
        parse_display_dt(v)
    except ValueError:
        raise ValueError("不是有效的日期时间") from None
    return v


Cutoff = Annotated[
    str | None,
    AfterValidator(_check_cutoff),
    Query(description="下单时间截止（含）：YYYY-MM-DD HH:mm[:ss]，北京时间", examples=["2026-10-24 23:00"]),
]


@router.get("/orders", response_model=OrdersPage, summary="订单列表（下单时间新→旧，分页）")
async def list_orders(
    session: SessionDep,
    user: CurrentUserDep,
    page: PageParamsDep,
    store_id: Annotated[str | None, Query(alias="storeId", description="门店编号 SH0031")] = None,
    status: Annotated[OrderStatus | None, Query(description="订单状态（中文）；「全部」时不传")] = None,
    channel: Annotated[str | None, Query(description="渠道；「全部」时不传")] = None,
    kw: Annotated[
        str | None, Query(description="订单号或门店名包含（区分大小写，同前端 includes；%、_ 按字面）")
    ] = None,
    cutoff: Cutoff = None,
) -> OrdersPage:
    until = before = None
    as_of = clock.now()
    if cutoff is not None:
        at = parse_display_dt(cutoff)
        if len(cutoff) == 16:  # 只到分钟：含这一整分钟
            before = at + timedelta(minutes=1)
        else:
            until = at
        as_of = min(at, as_of)
    repo = OrderRepo(session)
    stmt = repo.list_query(
        store=store_id, status=status, channel=channel, kw=kw, created_until=until, created_before=before
    )
    stmt = await apply_row_filter(stmt, user, "order", ORDER_COLUMNS, ORDER_LINKS)
    rows, total = await repo.list_page(stmt, page)
    # 页码越界 → 最后一页，page 字段也收拢（前端 paginate.ts:13）
    return OrdersPage.of(
        [OrderOut.model_validate(r) for r in rows], total, page, data_as_of=_to_minute(as_of)
    )


def _to_minute(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


@router.get("/orders/{order_id}", response_model=OrderDetailOut, summary="订单详情（含订单行）")
async def get_order(order_id: str, session: SessionDep, user: CurrentUserDep) -> OrderDetailOut:
    repo = OrderRepo(session)
    stmt = await apply_row_filter(repo.by_code_query(order_id), user, "order", ORDER_COLUMNS, ORDER_LINKS)
    obj = (await session.scalars(stmt)).first()
    if obj is None:
        raise NotFound("订单不存在", details={"id": order_id})
    return OrderDetailOut.model_validate(obj)
