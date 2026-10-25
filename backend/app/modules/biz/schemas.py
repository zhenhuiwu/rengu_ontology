"""对外 Schema。

字段对齐前端 mock ORDERS：id / storeId / createdAt / shippedAt / lines[{sku, qty, price}] / member。
列表项不带 lines，只带 lineCount（= 详情 lines 的条数，首页“商品数”列，Home.vue:54/144）；详情带完整 lines。
"""

from decimal import Decimal

from pydantic import AliasPath, Field, field_serializer

from app.common.pagination import Page
from app.core.schema import ApiModel, CodeId, DateTimeMin, DateTimeSec
from app.modules.biz.models import OrderStatus


class OrderLineOut(ApiModel):
    line_no: int
    sku: str = Field(validation_alias=AliasPath("product", "code"))
    name: str = Field(validation_alias=AliasPath("product", "name"))
    qty: Decimal
    price: Decimal

    @field_serializer("qty", "price")
    def _num(self, v: Decimal) -> float:
        return float(v)


class OrderOut(ApiModel):
    id: CodeId
    store_id: str = Field(validation_alias=AliasPath("store", "code"))
    store_name: str = Field(validation_alias=AliasPath("store", "name"))
    channel: str
    status: OrderStatus
    created_at: DateTimeSec = Field(validation_alias="created_at_biz")
    shipped_at: DateTimeSec | None = None
    amount: Decimal
    discount: Decimal
    warehouse: str | None = None
    waybill: str | None = None
    member: str | None = Field(None, validation_alias="member_phone")
    is_test: bool = False
    line_count: int = 0  # 订单行数（OrderRepo 子查询填充）

    @field_serializer("amount", "discount")
    def _num(self, v: Decimal) -> float:
        return float(v)


class OrderDetailOut(OrderOut):
    lines: list[OrderLineOut]


class OrdersPage(Page[OrderOut]):
    """GET /orders：分页 + dataAsOf（"YYYY-MM-DD HH:mm"，北京时间）。

    dataAsOf = 本次结果的数据截止时刻：传了 cutoff 取 min(cutoff, 当前时间)，否则取当前时间，都截到分钟。
    首页传 cutoff=2026-10-24 23:00 时 dataAsOf = "2026-10-24 23:00"，与页头“数据截至 2026-10-24 23:00”一致。
    """

    data_as_of: DateTimeMin
