"""通用对象搜索 / 详情（explore_api.py，generic-object-explorer.md）+ O4 按通用筛选提问。"""

import pytest
from sqlalchemy import false

from app.seeds.loader import run_seeds

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    # 元数据读静态 object_type / property，要 static seed（versioning 先登记 section，同 test_qa_tuning_eval）
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(users, ["versioning", "static", "qa", "biz"])
    await users.flush()
    return client


async def _search(c, code, **body):
    r = await c.post(f"{B}/object-types/{code}/search", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_store_rows_facets_and_scope(qa):
    d = await _search(qa, "store", size=200)
    old = (await qa.post(f"{B}/objects/store/search", json={"size": 200})).json()
    assert d["bound"] and d["titleProp"] == "store_name"
    assert d["total"] == old["total"] == 95  # 同一套行级范围（华东大区）
    it = d["items"][0]
    assert set(it) == {"id", "title", "values"} and it["title"] == it["values"]["store_name"]
    assert it["values"]["store_id"] == it["id"] and "store_name" in d["boundProps"]
    fmt = next(f for f in d["facets"] if f["key"] == "format")
    assert fmt["kind"] == "enum" and sum(v["count"] for v in fmt["values"]) == d["total"]
    opened = next(f for f in d["facets"] if f["key"] == "opened_at")
    assert opened["kind"] == "range" and opened["min"] <= opened["max"]
    # 分面联动：选了 format 后 format 自己的计数不变，结果数 = 该取值的计数
    v0 = fmt["values"][0]
    d2 = await _search(qa, "store", filters={"format": [v0["value"]]}, sort="-area_sqm")
    assert d2["total"] == v0["count"]
    assert next(f for f in d2["facets"] if f["key"] == "format") == fmt
    areas = [x["values"]["area_sqm"] for x in d2["items"] if x["values"]["area_sqm"] is not None]
    assert areas == sorted(areas, reverse=True)
    # 区间（日期含端点）+ 关键词
    d3 = await _search(qa, "store", filters={"opened_at": {"min": opened["min"], "max": opened["min"]}})
    assert d3["total"] >= 1 and all(x["values"]["opened_at"] == opened["min"] for x in d3["items"])
    kw = it["values"]["store_name"][:3]
    d4 = await _search(qa, "store", kw=kw, with_facets=False)
    assert d4["total"] >= 1 and d4["facets"] == []


async def test_order_product_unbound_and_errors(qa):
    o = await _search(qa, "order", filters={"created_at": {"min": "2000-01-01", "max": "2100-12-31"}}, size=5)
    assert o["bound"] and o["total"] > 0 and o["items"][0]["values"]["store_id"]
    p = await _search(qa, "product", sort="retail_price", size=5)
    prices = [x["values"]["retail_price"] for x in p["items"]]
    assert p["total"] > 0 and prices == sorted(prices)
    m = await _search(qa, "member")
    assert m["bound"] is False and m["items"] == [] and m["titleProp"]
    assert (await qa.post(f"{B}/object-types/nope/search", json={})).status_code == 404
    r = await qa.post(f"{B}/object-types/store/search", json={"filters": {"phone": ["1"]}})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    r = await qa.post(f"{B}/object-types/store/search", json={"filters": {"opened_at": {"min": "x"}}})
    assert r.status_code == 422


async def test_detail_scope_and_ask(qa, monkeypatch):
    d = await _search(qa, "store", size=200)
    sid = d["items"][0]["id"]
    r = await qa.get(f"{B}/object-types/store/objects/{sid}")
    assert r.status_code == 200, r.text
    assert r.json()["values"]["store_id"] == sid and r.json()["titleProp"] == "store_name"

    # 范围外：把行级范围换成“全拒”，存在的对象报 OUT_OF_SCOPE 而不是 404
    async def deny(stmt, *_a):
        return stmt.where(false())

    with monkeypatch.context() as m:
        m.setattr("app.modules.qa.explore_api.apply_row_filter", deny)
        r = await qa.get(f"{B}/object-types/store/objects/{sid}")
        assert r.status_code == 403 and r.json()["code"] == "OUT_OF_SCOPE"
        assert (await _search(qa, "store"))["total"] == 0
    assert (await qa.get(f"{B}/object-types/store/objects/NOPE")).status_code == 404
    assert (await qa.get(f"{B}/object-types/member/objects/x")).status_code == 404
    # O4：按通用筛选提问 = 范围内命中的对象
    fmt = next(f for f in d["facets"] if f["key"] == "format")["values"][0]
    body = {
        "scope": {"kind": "store", "label": "门店", "query": {"filters": {"format": [fmt["value"]]}}},
        "question": "销售额怎么样",
    }
    r = await qa.post(f"{B}/scoped-ask", json=body)
    assert r.status_code == 200, r.text
    a = r.json()
    assert f"这 {fmt['count']} 家门店" in a["text"]
    body["scope"]["query"] = {"kw": "不存在的门店名xyz"}
    a = (await qa.post(f"{B}/scoped-ask", json=body)).json()
    assert a["rows"] == [] and "没有" in a["text"]


async def test_export_csv(qa):
    d = await _search(qa, "store", size=2)
    ids = [x["id"] for x in d["items"]]
    r = await qa.post(
        f"{B}/object-types/store/export", json={"ids": ids, "props": ["city", "phone", "store_id"]}
    )
    assert r.status_code == 200, r.text
    lines = r.content.decode("utf-8-sig").strip().split("\n")
    assert len(lines) == 3 and lines[0].startswith('"门店名称"') and "电话" not in lines[0]
    pv = await qa.post(f"{B}/object-types/store/export?previewAs=U010763", json={})
    assert pv.status_code == 403
