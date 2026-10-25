"""biz 只读接口（演示业务数据）。挂载在 /api/biz/v1 [推断] 下。

查询统一经 apply_row_filter 注入行级过滤（对象类型 order，一跳 order_belongs_to_store）。
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.common.auth import CurrentUserDep
from app.common.pagination import Page, PageParamsDep
from app.common.row_filter import apply_row_filter
from app.core.errors import NotFound
from app.db.session import SessionDep
from app.modules.biz.governance import ORDER_COLUMNS, ORDER_LINKS
from app.modules.biz.models import OrderStatus
from app.modules.biz.repo import OrderRepo
from app.modules.biz.schemas import OrderDetailOut, OrderOut

router = APIRouter(tags=["biz"])


@router.get("/orders", response_model=Page[OrderOut], summary="订单列表（下单时间新→旧，分页）")
async def list_orders(
    session: SessionDep,
    user: CurrentUserDep,
    page: PageParamsDep,
    store_id: Annotated[str | None, Query(alias="storeId", description="门店编号 SH0031")] = None,
    status: Annotated[OrderStatus | None, Query(description="订单状态（中文）")] = None,
    channel: str | None = None,
) -> Page[OrderOut]:
    repo = OrderRepo(session)
    stmt = repo.list_query(store=store_id, status=status, channel=channel)
    stmt = await apply_row_filter(stmt, user, "order", ORDER_COLUMNS, ORDER_LINKS)
    rows, total = await repo.list_page(stmt, page)
    return Page(items=[OrderOut.model_validate(r) for r in rows], total=total, page=page.page, size=page.size)


@router.get("/orders/{order_id}", response_model=OrderDetailOut, summary="订单详情（含订单行）")
async def get_order(order_id: str, session: SessionDep, user: CurrentUserDep) -> OrderDetailOut:
    repo = OrderRepo(session)
    stmt = await apply_row_filter(repo.by_code_query(order_id), user, "order", ORDER_COLUMNS, ORDER_LINKS)
    obj = (await session.scalars(stmt)).first()
    if obj is None:
        raise NotFound("订单不存在", details={"id": order_id})
    return OrderDetailOut.model_validate(obj)
