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


# ---- M7 lineCount / M9 kw、cutoff、dataAsOf（首页 Home.vue:32-46、:54、:103） ----

STORE_NAME = {s["id"]: s["name"] for s in STORES}
LINES_OF: dict[str, int] = {}
for _o in ORDERS:  # 重复订单号：接口详情取 id 最小（seed 顺序第一条）
    LINES_OF.setdefault(_o["id"], len(_o["lines"]))


async def _all(client, **params):
    r = await client.get("/api/biz/v1/orders", params={"size": 200, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    items = list(body["items"])
    for p in range(2, -(-body["total"] // 200) + 1):
        items += (await client.get("/api/biz/v1/orders", params={"size": 200, "page": p, **params})).json()[
            "items"
        ]
    return body, items


async def test_list_line_count_matches_mock_and_detail(client, biz):
    _, items = await _all(client)
    assert len(items) == 1286 and all("lines" not in i for i in items)
    want = Counter((o["id"], len(o["lines"])) for o in ORDERS)
    assert Counter((i["id"], i["lineCount"]) for i in items) == want
    d = (await client.get("/api/biz/v1/orders/SO202610240889017")).json()
    assert d["lineCount"] == len(d["lines"]) == LINES_OF["SO202610240889017"]


async def test_list_line_count_is_one_query(client, biz, engine):
    """列表不按单加载订单行：一页 = count + 一条带子查询的 select，与页大小无关。"""
    from sqlalchemy import event

    seen: list[str] = []

    def _on(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _on)
    try:
        await client.get("/api/biz/v1/orders", params={"size": 50})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on)
    order_sql = [s for s in seen if "order_line" in s or "biz_order" in s]
    assert len(order_sql) == 2, order_sql  # selectin 加载 lines 会多一条 “order_line.order_id IN (…)”


async def test_line_count_respects_row_filter(client, biz, monkeypatch):
    import app.common.auth as auth

    async def _rf(user, object_type):
        return {"field": "channel", "op": "=", "value": "美团闪购"}

    monkeypatch.setattr(auth, "row_filter", _rf)
    body, items = await _all(client)
    meituan = [o for o in ORDERS if o["channel"] == "美团闪购"]
    assert body["total"] == len(meituan) > 0 and {i["channel"] for i in items} == {"美团闪购"}
    assert sum(i["lineCount"] for i in items) == sum(len(o["lines"]) for o in meituan)
    # 筛选参数和行级过滤叠加：渠道 = 饿了么 → 0
    assert (await client.get("/api/biz/v1/orders", params={"channel": "饿了么"})).json()["total"] == 0


async def test_kw_matches_code_or_store_name_case_insensitive(client, biz):
    code = ORDERS[0]["id"]
    body, items = await _all(client, kw=code.lower())  # so2026… 不区分大小写
    assert body["total"] == sum(o["id"] == code for o in ORDERS) >= 1 and {i["id"] for i in items} == {code}
    body, items = await _all(client, kw="张江")
    want = [o for o in ORDERS if "张江" in o["id"] or "张江" in STORE_NAME[o["storeId"]]]
    assert body["total"] == len(want) > 0 and all("张江" in i["storeName"] for i in items)
    # 子串：订单号片段
    frag = code[2:10]
    want = [o for o in ORDERS if frag in o["id"] or frag in STORE_NAME[o["storeId"]]]
    assert (await client.get("/api/biz/v1/orders", params={"kw": frag})).json()["total"] == len(want)


async def test_kw_escapes_like_wildcards(client, biz):
    for kw in ("%", "_", "SO%", "S_2026", "\\"):
        assert (await client.get("/api/biz/v1/orders", params={"kw": kw})).json()["total"] == 0, kw
    s = await biz.scalar(select(Store).where(Store.code == "SH0031"))
    s.name = "Ab_C%店"
    await biz.flush()
    n = sum(o["storeId"] == "SH0031" for o in ORDERS)
    assert (await client.get("/api/biz/v1/orders", params={"kw": "b_c%"})).json()["total"] == n > 0
    assert (await client.get("/api/biz/v1/orders", params={"kw": "bxc"})).json()["total"] == 0


async def test_cutoff_inclusive_both_formats(client, biz):
    # mock 里正好有一单 2026-10-24 23:00:58：分钟格式含整分钟（前端 CUTOFF='2026-10-24 23:00:59'）
    def n(t):
        return sum(o["createdAt"] <= t for o in ORDERS)

    async def total(cutoff):
        return (await client.get("/api/biz/v1/orders", params={"cutoff": cutoff, "size": 1})).json()["total"]

    assert await total("2026-10-24 23:00") == n("2026-10-24 23:00:59") == 1043
    assert await total("2026-10-24 23:00:59") == 1043
    assert await total("2026-10-24 23:00:58") == 1043  # 等于也算（<=）
    assert await total("2026-10-24 23:00:57") == n("2026-10-24 23:00:57") == 1042
    assert await total("2026-10-24 22:59") == n("2026-10-24 22:59:59") == 1042
    # 与其它筛选叠加：首页 = cutoff + status/channel/kw
    r = await client.get(
        "/api/biz/v1/orders", params={"cutoff": "2026-10-24 23:00", "status": "已出库", "kw": "张江"}
    )
    want = [
        o
        for o in ORDERS
        if o["createdAt"] <= "2026-10-24 23:00:59"
        and o["status"] == "已出库"
        and ("张江" in o["id"] or "张江" in STORE_NAME[o["storeId"]])
    ]
    assert r.json()["total"] == len(want)


async def test_cutoff_is_beijing_time(client, biz):
    # naive 值按北京时间（若按 UTC 解读，15:00 会多出 8 小时的订单）
    r = await client.get("/api/biz/v1/orders", params={"cutoff": "2026-10-24 15:00", "size": 1})
    assert r.json()["total"] == sum(o["createdAt"] <= "2026-10-24 15:00:59" for o in ORDERS)


@pytest.mark.parametrize(
    "bad",
    [
        "2026-10-24",
        "2026-10-24T23:00",
        "2026-10-24 23:00+08:00",
        "2026-13-01 00:00",
        "2026-10-24 24:00",
        "x",
        "",
    ],
)
async def test_cutoff_bad_format_is_422(client, biz, bad):
    r = await client.get("/api/biz/v1/orders", params={"cutoff": bad})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "VALIDATION_FAILED", body
    assert [f["field"] for f in body["details"]["fields"]] == ["cutoff"]


async def test_data_as_of(client, biz):
    # 测试时钟 RENGU_FIXED_NOW = 2026-10-25 15:42（北京时间）
    body = (await client.get("/api/biz/v1/orders", params={"size": 1})).json()
    assert body["dataAsOf"] == "2026-10-25 15:42"
    body = (await client.get("/api/biz/v1/orders", params={"size": 1, "cutoff": "2026-10-24 23:00"})).json()
    assert body["dataAsOf"] == "2026-10-24 23:00"  # = 页头“数据截至 2026-10-24 23:00”（Home.vue:103）
    body = (
        await client.get("/api/biz/v1/orders", params={"size": 1, "cutoff": "2026-10-24 23:00:59"})
    ).json()
    assert body["dataAsOf"] == "2026-10-24 23:00"
    body = (await client.get("/api/biz/v1/orders", params={"size": 1, "cutoff": "2027-01-01 00:00"})).json()
    assert body["dataAsOf"] == "2026-10-25 15:42"  # 截止时间在未来：取当前时间
    assert set(body) == {"items", "total", "page", "size", "dataAsOf"}
