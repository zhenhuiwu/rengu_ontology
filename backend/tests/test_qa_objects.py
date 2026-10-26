"""R3 对象搜索 / R4 对象详情 O2–O7、O9（qa-pages 流）。"""

import pytest

from app.modules.qa.objects_api import fmt_money, fmt_pct, js_hash, seeded, store_sales, v8_random_sort
from app.seeds.loader import run_seeds

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    await run_seeds(users, ["biz", "qa"])
    await users.flush()
    return client


def test_js_ports():
    assert fmt_money(1234.5) == "1,234.50" and fmt_money(0.125) == "0.13" and fmt_money(None) == "--"
    assert fmt_pct(0.857142) == "85.71%"
    # 与 node 下 [...12].sort(() => r() - 0.5) 的结果一致（V8 二分插入排序）
    r = seeded(js_hash("SH0031") + 3)
    assert v8_random_sort(list(range(12)), r)[:8] == [0, 1, 10, 5, 7, 3, 4, 8]
    assert store_sales("FZ0011") == (16, -13.7)  # 问数结果表里的原值


async def test_search_store_facets_and_paging(qa):
    r = await qa.post(f"{B}/objects/store/search", json={"kw": "", "facets": {}, "page": 1, "size": 16})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 95 and len(d["items"]) == 16 and d["page"] == 1
    assert d["scopeText"].startswith("可见范围：华东大区")
    it = d["items"][0]
    assert set(it) == {"id", "title", "cells"} and "sales" in it["cells"] and "code" in it["cells"]
    region = next(f for f in d["facets"] if f["key"] == "region")
    assert [v["v"] for v in region["vals"]][:5] == [
        "华东一区",
        "华东二区",
        "华东三区",
        "华东四区",
        "华东五区",
    ]
    assert sum(v["n"] for v in region["vals"]) == d["total"]
    # 本分面不参与自己的计数；其他分面生效
    r2 = await qa.post(f"{B}/objects/store/search", json={"facets": {"region": ["华东一区"]}})
    d2 = r2.json()
    region2 = next(f for f in d2["facets"] if f["key"] == "region")
    assert region2["vals"] == region["vals"]
    assert d2["total"] == next(v["n"] for v in region["vals"] if v["v"] == "华东一区")
    city2 = next(f for f in d2["facets"] if f["key"] == "city")
    assert sum(v["n"] for v in city2["vals"]) == d2["total"]
    # 关键词（不区分大小写）+ 超出页码回到最后一页
    r3 = await qa.post(f"{B}/objects/store/search", json={"kw": "sh-hk", "page": 99})
    d3 = r3.json()
    assert d3["total"] >= 1 and all(x["cells"]["code"].startswith("SH-HK") for x in d3["items"])
    assert d3["page"] == 1


async def test_search_order_and_product(qa):
    d = (await qa.post(f"{B}/objects/order/search", json={"facets": {"ostatus": ["已取消"]}})).json()
    assert d["total"] > 0 and all(x["cells"]["status"] == "已取消" for x in d["items"])
    assert all(x["cells"]["store"] != "--" for x in d["items"])
    p = (await qa.post(f"{B}/objects/product/search", json={})).json()
    assert p["total"] == 12 and {f["key"] for f in p["facets"]} == {"cat", "brand"}
    bad = await qa.post(f"{B}/objects/nope/search", json={})
    assert bad.status_code == 404


async def test_export_csv(qa):
    r = await qa.post(f"{B}/objects/store/export", json={"facets": {"region": ["华东四区"]}})
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"\xef\xbb\xbf")
    lines = r.content.decode("utf-8-sig").strip().split("\n")
    assert lines[0].startswith('"门店名称","门店编码"')
    assert "attachment" in r.headers["content-disposition"]
    n = len(lines) - 1
    sel = await qa.post(f"{B}/objects/store/export", json={"ids": ["SH0031", "NOPE"]})
    assert len(sel.content.decode("utf-8-sig").strip().split("\n")) == 2
    assert n > 0
    # 历史版本下可导出；预览下 EXPORT_FORBIDDEN
    assert (await qa.post(f"{B}/objects/store/export?ver=v1", json={})).status_code == 200
    pv = await qa.post(f"{B}/objects/store/export?previewAs=U010763", json={})
    assert pv.status_code == 403 and pv.json()["code"] == "EXPORT_FORBIDDEN"
    # 搜索两种只读模式都能用
    assert (await qa.post(f"{B}/objects/store/search?previewAs=U010763", json={})).status_code == 200
    assert (await qa.post(f"{B}/objects/store/search?ver=v1", json={})).status_code == 200


async def test_scoped_ask(qa):
    body = {
        "scope": {"kind": "store", "label": "门店", "filter": {"facets": {"region": ["华东四区"]}}},
        "question": "合计",
    }
    r = await qa.post(f"{B}/scoped-ask", json=body)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["text"].startswith("这 ") and "销售额（不含税） v3" in a["text"]
    assert "区域 = 华东四区" in a["basis"] and "数据截至 2026-10-24 23:00" in a["basis"]
    assert 0 < len(a["rows"]) <= 5
    single = {
        "scope": {
            "kind": "store",
            "label": "门店 x",
            "single": {"kind": "store", "id": "SH0031", "title": "x"},
        }
    }
    s = (await qa.post(f"{B}/scoped-ask", json=single | {"question": "排第几"})).json()
    assert "排第" in s["text"] and any(x["note"] == "当前门店" for x in s["rows"])
    o = await qa.post(
        f"{B}/scoped-ask",
        json={"scope": {"kind": "order", "label": "订单", "ids": ["SO202610250794580"]}, "question": "q"},
    )
    assert o.json()["text"].startswith("这 1 单实付金额合计 274.50 元") and "已选 1 个" in o.json()["basis"]
    # 预览可问，历史版本只读
    assert (await qa.post(f"{B}/scoped-ask?previewAs=U010763", json=body)).status_code == 200
    ro = await qa.post(f"{B}/scoped-ask?ver=v1", json=body)
    assert ro.status_code == 403 and ro.json()["code"] == "READONLY"


async def test_detail_store_and_drawer(qa):
    r = await qa.get(f"{B}/objects/store/SH0031")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kindLabel"] == "门店" and d["id"] == "SH0031"
    assert [g["title"] for g in d["groups"]][:2] == ["基本信息", "经营"]
    assert (
        d["kpis"][0]["label"] == "9 月销售额（不含税）" and d["kpis"][0]["caliber"] == "销售额（不含税） v3"
    )
    assert any(k["caliber"] == "销售额（含税） v2" for k in d["kpis"]) and len(d["kpis"]) == 5
    assert len(d["quickQuestions"]) == 3 and d["scopeLabel"] == "数据范围：全部门店"
    dr = (await qa.get(f"{B}/objects/store/SH0031?view=drawer")).json()
    assert "kpis" not in dr and dr["groups"] == d["groups"]
    # 扩充门店也能打开
    assert (await qa.get(f"{B}/objects/store/SH0201")).status_code == 200
    assert (await qa.get(f"{B}/objects/store/NOPE")).status_code == 404
    reg = (await qa.get(f"{B}/objects/region/REG-EC")).json()
    assert (
        reg["owner"] == "陈杰" and reg["repeatShare"] == "62.0%" and reg["kpis"][1]["caliber"].endswith("v3")
    )
    order = (await qa.get(f"{B}/objects/order/SO202610250794580")).json()
    assert order["code"] == "饿了么" and order["status"]["text"] == "已签收"
    prod = (await qa.get(f"{B}/objects/product/1002387")).json()
    assert prod["title"] == "农夫山泉饮用天然水"


async def test_related_timeline_actions(qa):
    rel = (await qa.get(f"{B}/objects/store/SH0031/related")).json()
    keys = [g["key"] for g in rel]
    assert keys[:3] == ["region", "peer", "product"]
    prod = next(g for g in rel if g["key"] == "product")
    assert prod["total"] == 8 and prod["size"] == 8
    reg = (await qa.get(f"{B}/objects/region/REG-EC/related?group=rel-store&page=2")).json()
    assert len(reg) == 1 and reg[0]["title"] == "下属门店" and reg[0]["page"] == 2 and reg[0]["total"] > 8
    tl = (await qa.get(f"{B}/objects/store/SH0031/timeline")).json()
    assert tl["total"] == 21 and len(tl["items"]) == 8 and tl["unknownLabel"] == "结果未知 · 待对账"
    times = [x["time"] for x in tl["items"]]
    assert times == sorted(times, reverse=True)
    rtl = (await qa.get(f"{B}/objects/region/REG-EC/timeline?size=6")).json()
    assert rtl["total"] == 26 and len(rtl["items"]) == 6
    ptl = (await qa.get(f"{B}/objects/product/1002387/timeline?size=50")).json()
    assert sum(1 for x in ptl["items"] if x.get("status") == "unknown") <= 1
    acts = (await qa.get(f"{B}/objects/order/SO202610250794580/actions")).json()
    assert acts[0]["blocked"] == "订单已出库，不能补发" and acts[1]["approver"] == "赵磊"
    racts = (await qa.get(f"{B}/objects/region/REG-EC/actions")).json()
    assert [a["key"] for a in racts] == ["coupon", "follow", "budget"]
