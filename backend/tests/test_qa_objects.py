"""R3 对象搜索 / R4 对象详情 O2–O7、O9（qa-pages 流）。"""

import pytest

from app.modules.qa.objects_api import fmt_money, fmt_pct
from tests.qa_access_support import seed_qa_access

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await users.flush()
    return client


def test_js_ports():
    assert fmt_money(1234.5) == "1,234.50" and fmt_money(0.125) == "0.13" and fmt_money(None) == "--"
    assert fmt_pct(0.857142) == "85.71%"


async def test_search_store_facets_and_paging(qa):
    r = await qa.post(f"{B}/objects/store/search", json={"kw": "", "facets": {}, "page": 1, "size": 16})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 95 and len(d["items"]) == 16 and d["page"] == 1
    assert d["scopeText"] == "数据范围：全部门店"
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
    assert r.status_code == 503, r.text
    a = r.json()
    assert a["code"] == "DATA_NOT_CONFIGURED"
    single = {
        "scope": {
            "kind": "store",
            "label": "门店 x",
            "single": {"kind": "store", "id": "SH0031", "title": "x"},
        }
    }
    s = (await qa.post(f"{B}/scoped-ask", json=single | {"question": "排第几"})).json()
    assert s["code"] == "DATA_NOT_CONFIGURED"
    o = await qa.post(
        f"{B}/scoped-ask",
        json={
            "scope": {"kind": "order", "label": "订单", "ids": ["SO202610250794580"]},
            "question": "实付金额合计",
        },
    )
    assert (
        o.json()["text"].startswith("当前可见的 1 单实付金额合计 274.50 元")
        and "已选 1 个" in o.json()["basis"]
    )
    # 预览可问，历史版本只读
    assert (await qa.post(f"{B}/scoped-ask?previewAs=U010763", json=body)).status_code == 503
    ro = await qa.post(f"{B}/scoped-ask?ver=v1", json=body)
    assert ro.status_code == 403 and ro.json()["code"] == "READONLY"


async def test_detail_store_and_drawer(qa):
    r = await qa.get(f"{B}/objects/store/SH0031")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kindLabel"] == "门店" and d["id"] == "SH0031"
    assert [g["title"] for g in d["groups"]][:2] == ["基本信息", "经营"]
    assert d["kpis"][0]["label"] == "实付金额" and d["kpis"][0]["caliber"] is None
    assert all(k["caliber"] is None for k in d["kpis"]) and len(d["kpis"]) == 4
    assert d["quickQuestions"] == [] and d["scopeLabel"] == "数据范围：全部门店"
    assert not d["analyticsConfigured"] and "尚未配置" in d["analyticsNote"]
    dr = (await qa.get(f"{B}/objects/store/SH0031?view=drawer")).json()
    assert "kpis" not in dr and dr["groups"] == d["groups"]
    # 已灌入数据库的扩充种子记录继续保留；不再从内存补记录。
    assert (await qa.get(f"{B}/objects/store/SH0201")).status_code == 200
    assert (await qa.get(f"{B}/objects/store/NOPE")).status_code == 404
    reg = await qa.get(f"{B}/objects/region/REG-EC")
    assert reg.status_code == 503 and reg.json()["code"] == "DATA_NOT_CONFIGURED"
    order = (await qa.get(f"{B}/objects/order/SO202610250794580")).json()
    assert order["code"] == "饿了么" and order["status"]["text"] == "已签收"
    prod = (await qa.get(f"{B}/objects/product/1002387")).json()
    assert prod["title"] == "农夫山泉饮用天然水"


async def test_related_timeline_actions(qa):
    rel = (await qa.get(f"{B}/objects/store/SH0031/related")).json()
    keys = [g["key"] for g in rel]
    assert "peer" in keys and "region" not in keys and "product" not in keys
    reg = await qa.get(f"{B}/objects/region/REG-EC/related?group=rel-store&page=2")
    assert reg.status_code == 503 and reg.json()["code"] == "DATA_NOT_CONFIGURED"
    tl = (await qa.get(f"{B}/objects/store/SH0031/timeline")).json()
    assert tl["total"] == 0 and tl["items"] == [] and not tl["configured"]
    times = [x["time"] for x in tl["items"]]
    assert times == sorted(times, reverse=True)
    rtl = await qa.get(f"{B}/objects/region/REG-EC/timeline?size=6")
    assert rtl.status_code == 503 and rtl.json()["code"] == "DATA_NOT_CONFIGURED"
    ptl = (await qa.get(f"{B}/objects/product/1002387/timeline?size=50")).json()
    assert ptl["items"] == [] and not ptl["configured"]
    acts = (await qa.get(f"{B}/objects/order/SO202610250794580/actions")).json()
    assert acts == []
    racts = await qa.get(f"{B}/objects/region/REG-EC/actions")
    assert racts.status_code == 503 and racts.json()["code"] == "DATA_NOT_CONFIGURED"
