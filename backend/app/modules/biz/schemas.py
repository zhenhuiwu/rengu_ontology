"""对外 Schema。

字段对齐前端 mock ORDERS：id / storeId / createdAt / shippedAt / lines[{sku, qty, price}] / member。
"""

from decimal import Decimal

from pydantic import AliasPath, Field, field_serializer

from app.core.schema import ApiModel, CodeId, DateTimeSec
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

    @field_serializer("amount", "discount")
    def _num(self, v: Decimal) -> float:
        return float(v)


class OrderDetailOut(OrderOut):
    lines: list[OrderLineOut]
