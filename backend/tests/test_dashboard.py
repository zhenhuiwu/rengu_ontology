"""首页看板：/dashboard/kpis、/stores/ranking 与 Home.vue 的 mock 计算一致；未登录 401。"""

import json
import math

import pytest

from app.core.config import get_settings
from app.seeds.loader import run_seeds

_J = get_settings().seeds_json_dir
ORDERS = json.loads((_J / "common" / "ORDERS.json").read_text("utf-8"))
STORES12 = json.loads((_J / "common" / "ALL_STORES.json").read_text("utf-8"))[:12]  # mock STORES = 核心 12 家
METRICS = {m["id"]: m for m in json.loads((_J / "static" / "METRICS.json").read_text("utf-8"))}
CUTOFF = "2026-10-24 23:00:59"  # Home.vue:38
P = "/api/biz/v1"


def ex_tax(v: float) -> float:
    return math.floor(v / 1.13 * 100 + 0.5) / 100


def mock_ranking() -> list[dict]:
    """Home.vue:77-81 原样复刻。"""
    cut = [o for o in ORDERS if o["createdAt"] <= CUTOFF]
    rows = []
    for s in STORES12:
        os_ = [o for o in cut if o["storeId"] == s["id"]]
        amt = 0.0
        for o in os_:
            amt += o["amount"]
        rows.append(
            {"id": s["id"], "name": s["name"], "city": s["city"], "cnt": len(os_), "amount": ex_tax(amt)}
        )
    rows.sort(key=lambda r: -r["amount"])
    return [{**r, "rank": i + 1} for i, r in enumerate(rows)]


@pytest.fixture
async def seeded(session):
    await run_seeds(session, ["admin", "versioning", "static", "biz"])
    await session.commit()
    return session


async def test_kpis_match_mock(client, seeded):
    r = await client.get(P + "/dashboard/kpis", params={"cutoff": "2026-10-24 23:00"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dataAsOf"] == "2026-10-24 23:00"
    ids = ["gmv", "order_cnt", "gross_margin", "ship_ontime"]
    assert [m["id"] for m in body["items"]] == ids
    for m in body["items"]:
        want = METRICS[m["id"]]
        assert set(m) == {"id", "name", "unit", "value", "prev"}
        assert (m["name"], m["unit"], m["value"], m["prev"]) == (
            want["name"],
            want["unit"],
            want["value"],
            want["prev"],
        )


async def test_kpis_default_ids_and_order(client, seeded):
    body = (await client.get(P + "/dashboard/kpis")).json()
    assert [m["id"] for m in body["items"]] == ["gmv", "order_cnt", "gross_margin", "ship_ontime"]
    body = (await client.get(P + "/dashboard/kpis", params={"ids": "atv,gmv,nope"})).json()
    assert [m["id"] for m in body["items"]] == ["atv", "gmv"]


async def test_ranking_matches_mock(client, seeded):
    want = mock_ranking()
    r = await client.get(P + "/stores/ranking", params={"to": CUTOFF, "page": 1, "size": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(want) == 12
    assert (body["page"], body["size"], body["dataAsOf"]) == (1, 10, "2026-10-24 23:00")
    assert body["items"] == want[:10]
    p2 = (
        await client.get(P + "/stores/ranking", params={"to": "2026-10-24 23:00", "page": 2, "size": 10})
    ).json()
    assert p2["items"] == want[10:] and p2["page"] == 2
    over = (await client.get(P + "/stores/ranking", params={"to": CUTOFF, "page": 9, "size": 10})).json()
    assert over["page"] == 2 and over["items"] == want[10:]  # 越界收拢到最后一页


async def test_ranking_without_cutoff_counts_all_orders(client, seeded):
    body = (await client.get(P + "/stores/ranking", params={"size": 200})).json()
    assert sum(i["cnt"] for i in body["items"]) == len(ORDERS)


async def test_ranking_bad_param_422(client, seeded):
    r = await client.get(P + "/stores/ranking", params={"to": "2026/10/24"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"


@pytest.mark.parametrize("url", ["/dashboard/kpis", "/stores/ranking"])
async def test_unauthenticated_401(client, seeded, url):
    r = await client.get(P + url, headers={"X-Demo-As": "U999999"})
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED"


async def test_viewer_ok(client, seeded):
    h = {"X-Demo-As": "U010006"}
    assert (await client.get(P + "/dashboard/kpis", headers=h)).status_code == 200
    assert (await client.get(P + "/stores/ranking", headers=h)).json()["total"] == 12
