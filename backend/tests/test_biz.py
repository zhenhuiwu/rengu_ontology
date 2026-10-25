"""biz 演示数据：seed 与 mock 一致（含两组重复订单号）、大区派生、is_test、订单接口与行级过滤。"""

import json
from collections import Counter

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.biz.models import BizOrder, OrderLine, Product, Store
from app.modules.biz.seeds import big_region_of
from app.seeds.loader import run_seeds

_DIR = get_settings().seeds_json_dir / "common"
ORDERS = json.loads((_DIR / "ORDERS.json").read_text("utf-8"))
STORES = json.loads((_DIR / "ALL_STORES.json").read_text("utf-8"))
DUPS = sorted(k for k, n in Counter(o["id"] for o in ORDERS).items() if n > 1)


@pytest.fixture
async def biz(users):
    await run_seeds(users, ["biz"])
    await users.flush()
    return users


def _count(model):
    return select(func.count()).select_from(model)


async def test_seed_counts_and_idempotent(biz):
    await run_seeds(biz, ["biz"])
    assert await biz.scalar(_count(Store)) == len(STORES) == 95
    assert await biz.scalar(_count(BizOrder)) == len(ORDERS) == 1286
    assert await biz.scalar(_count(OrderLine)) == sum(len(o["lines"]) for o in ORDERS)
    assert await biz.scalar(_count(Product)) > 0


async def test_duplicate_order_codes_kept(biz):
    assert DUPS == ["SO202610220914198", "SO202610240889017"]
    for code in DUPS:
        rows = (await biz.scalars(select(BizOrder).where(BizOrder.code == code).order_by(BizOrder.id))).all()
        assert len(rows) == 2 and rows[0].created_at_biz != rows[1].created_at_biz


async def test_big_region_and_is_test(biz):
    assert big_region_of("华东一区") == "华东" and big_region_of("华南十二区") == "华南"
    assert set(await biz.scalars(select(Store.big_region).distinct())) == {"华东"}
    assert not await biz.scalar(select(func.count()).where(Store.is_test))
    assert not await biz.scalar(select(func.count()).where(BizOrder.is_test))


async def test_list_orders(client, biz):
    r = await client.get("/api/biz/v1/orders", params={"size": 5})
    body = r.json()
    assert r.status_code == 200 and body["total"] == 1286 and len(body["items"]) == 5
    assert [i["createdAt"] for i in body["items"]] == sorted(
        (i["createdAt"] for i in body["items"]), reverse=True
    )
    first = body["items"][0]
    assert set(first) >= {"id", "storeId", "storeName", "channel", "status", "createdAt", "amount", "isTest"}
    r = await client.get("/api/biz/v1/orders", params={"storeId": "NJ0076", "size": 100})
    want = sum(o["storeId"] == "NJ0076" for o in ORDERS)
    assert r.json()["total"] == want and all(i["storeId"] == "NJ0076" for i in r.json()["items"])
    r = await client.get("/api/biz/v1/orders", params={"status": "已出库", "size": 1})
    assert r.status_code == 200 and r.json()["total"] == sum(o["status"] == "已出库" for o in ORDERS)


async def test_order_detail_first_duplicate_by_id(client, biz):
    r = await client.get("/api/biz/v1/orders/SO202610240889017")
    body = r.json()
    first = next(o for o in ORDERS if o["id"] == "SO202610240889017")
    assert r.status_code == 200 and body["storeId"] == first["storeId"] == "SH0133"
    assert len(body["lines"]) == len(first["lines"])
    assert (await client.get("/api/biz/v1/orders/SO0")).status_code == 404


async def test_viewer_is_denied_by_row_filter_stub(client, biz):
    h = {"X-Demo-As": "U010006"}  # 李娜：非管理员，row_filter 桩 → 全部拒绝
    r = await client.get("/api/biz/v1/orders", headers=h)
    assert r.status_code == 200 and r.json()["total"] == 0
    assert (await client.get("/api/biz/v1/orders/SO202610240889017", headers=h)).status_code == 404
