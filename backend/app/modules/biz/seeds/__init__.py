"""biz 模块 seed（JSON 组 common）。

- common/ALL_STORES.json → store：95 店，code=id（SH0031），store_no=code（SH-PD-0031）；
  big_region [推断] 由 region 推出（华东一区→华东）；is_test 全 false [推断]。
- common/PRODUCTS.json → product：12 个 SKU，code=sku。
- common/ORDERS.json → biz_order：1286 单；storeId → store.id；createdAt/shippedAt 带秒；
  member → member_phone；is_test 全 false。
- common/ORDERS.json（lines）→ order_line：一单展开成多行，line_no 按 lines 顺序从 1 起；
  订单按 (code, created_at_biz) 找 id（订单号有重复）。

ALL_STORES = 12 家基础门店 + QA_EC 26 家 + 运行时补出来的 57 家（export-mock 导出的是最终全集）。
"""

import re
from datetime import date

from app.core.schema import parse_display_dt
from app.modules.biz.models import BizOrder, OrderLine, OrderStatus, Product, Store, StoreStatus
from app.seeds import Ref, Row, SeedContext, SeedSpec

JSON_MODULE = "common"

_REGION_RE = re.compile(r"^(.+?)[一二三四五六七八九十]+区$")


def big_region_of(region: str) -> str:
    """[推断] 华东一区 → 华东；不符合「X 几区」的原样返回。"""
    m = _REGION_RE.match(region)
    return m.group(1) if m else region


def _store(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "store_no": r["code"],
        "name": r["name"],
        "city": r["city"],
        "region": r["region"],
        "big_region": big_region_of(r["region"]),
        "format": r["format"],
        "manager": r.get("manager"),
        "area": r.get("area"),
        "opened_at": date.fromisoformat(r["openedAt"]),
        "status": StoreStatus(r["status"]).name,
        "is_test": False,
    }


def _product(r: Row, _ctx: SeedContext) -> Row:
    return {k: r.get(k) for k in ("barcode", "name", "spec", "category", "brand", "price", "unit")} | {
        "code": r["sku"]
    }


def _order(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "store_id": r["storeId"],
        "channel": r["channel"],
        "status": OrderStatus(r["status"]).name,
        "created_at_biz": parse_display_dt(r["createdAt"]),
        "shipped_at": parse_display_dt(r["shippedAt"]) if r.get("shippedAt") else None,
        "amount": r["amount"],
        "discount": r.get("discount") or 0,
        "warehouse": r.get("warehouse"),
        "waybill": r.get("waybill"),
        "member_phone": r.get("member"),
        "is_test": False,
    }


def _lines(r: Row, _ctx: SeedContext) -> list[Row]:
    order = (r["id"], parse_display_dt(r["createdAt"]))
    return [
        {"order_id": order, "line_no": i, "product_id": ln["sku"], "qty": ln["qty"], "price": ln["price"]}
        for i, ln in enumerate(r["lines"], start=1)
    ]


SEEDS: list[SeedSpec] = [
    SeedSpec(Store, "ALL_STORES.json", key=("code",), transform=_store, description="门店"),
    SeedSpec(Product, "PRODUCTS.json", key=("code",), transform=_product, description="商品"),
    SeedSpec(
        BizOrder,
        "ORDERS.json",
        key=("code", "created_at_biz"),
        transform=_order,
        refs={"store_id": Ref(Store, "code", nullable=False)},
        description="订单",
    ),
    SeedSpec(
        OrderLine,
        "ORDERS.json",
        key=("order_id", "line_no"),
        transform=_lines,
        refs={
            "order_id": Ref(BizOrder, ("code", "created_at_biz"), nullable=False),
            "product_id": Ref(Product, "code", nullable=False),
        },
        description="订单行",
    ),
]
