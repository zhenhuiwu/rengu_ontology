"""通用对象搜索 / 详情（explore_api.py，generic-object-explorer.md）+ O4 按通用筛选提问。"""

import pytest
from sqlalchemy import false

from tests.qa_access_support import seed_qa_access

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await users.flush()
    return client


async def _open(users, obj, *props):
    """静态 seed 只给登记过的属性开 filterable（store 的 opened_at 等没开）；区间筛选用例自己打开标志。"""
    from sqlalchemy import select, update

    from app.modules.static.models import ObjectType, Property

    tid = (await users.execute(select(ObjectType.id).where(ObjectType.code == obj))).scalar_one()
    await users.execute(
        update(Property)
        .where(Property.object_type_id == tid, Property.api_name.in_(props))
        .values(filterable=True)
    )
    await users.flush()
    users.expire_all()


async def _search(c, code, **body):
    r = await c.post(f"{B}/object-types/{code}/search", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_store_rows_facets_and_scope(qa, users):
    await _open(users, "store", "opened_at")
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


async def test_order_product_unbound_and_errors(qa, users):
    await _open(users, "order", "created_at")
    await _open(users, "store", "opened_at")
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
    assert r.status_code == 503, r.text
    a = r.json()
    assert a["code"] == "DATA_NOT_CONFIGURED" and a["details"]["visibleCount"] == fmt["count"]
    body["scope"]["query"] = {"kw": "不存在的门店名xyz"}
    a = (await qa.post(f"{B}/scoped-ask", json=body)).json()
    assert a["code"] == "DATA_NOT_CONFIGURED" and a["details"]["visibleCount"] == 0


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


async def _set(users, obj, prop, **vals):
    """改一个属性的标志（敏感 / 隐藏 / 可排序等），用例内生效。"""
    from sqlalchemy import select, update

    from app.modules.static.models import ObjectType, Property

    tid = (await users.execute(select(ObjectType.id).where(ObjectType.code == obj))).scalar_one()
    await users.execute(
        update(Property).where(Property.object_type_id == tid, Property.api_name == prop).values(**vals)
    )
    await users.flush()
    users.expire_all()


@pytest.mark.parametrize(
    "filters",
    [
        {"area_sqm": ["abc"]},
        {"area_sqm": {"min": "abc"}},
        {"area_sqm": {"min": True}},
        {"area_sqm": {"lo": 1}},
        {"city": [{"a": 1}]},
        {"city": [5]},
        {"city": [None]},
        {"city": "上海"},
        {"store_name": {"min": 1}},
        {"status": ["不存在的状态"]},
        {"opened_at": {"min": 20260101}},
    ],
)
async def test_filter_values_type_checked(qa, users, filters):
    await _open(users, "store", "area_sqm", "city", "store_name", "status", "opened_at")
    r = await qa.post(f"{B}/object-types/store/search", json={"filters": filters})
    assert r.status_code == 422, r.text
    j = r.json()
    key = next(iter(filters))
    assert j["code"] == "VALIDATION_FAILED" and j["details"]["fields"][0]["field"] == f"filters.{key}"


@pytest.mark.parametrize("flag", [{"is_sensitive": True}, {"visibility": "hidden"}])
async def test_sensitive_hidden_blocked_in_backend(qa, users, flag):
    await _open(users, "store", "city")
    await _set(users, "store", "city", sortable=True)
    assert (await _search(qa, "store", sort="city", size=1))["total"] > 0  # 打开标志前可排序
    d0 = await _search(qa, "store", size=1)
    city0 = d0["items"][0]["values"]["city"]
    await _set(users, "store", "city", **flag)
    # 筛选 / 排序：422，字段格式同静态
    r = await qa.post(f"{B}/object-types/store/search", json={"filters": {"city": [city0]}})
    assert r.status_code == 422 and r.json()["details"]["fields"][0]["field"] == "filters.city"
    r = await qa.post(f"{B}/object-types/store/search", json={"sort": "-city"})
    assert r.status_code == 422 and r.json()["details"]["fields"][0]["field"] == "sort"
    r = await qa.post(f"{B}/object-types/store/export", json={"filters": {"city": [city0]}})
    assert r.status_code == 422
    # 结果 / 详情 / boundProps / 分面 / 导出都不带
    d = await _search(qa, "store", size=1)
    assert "city" not in d["items"][0]["values"] and "city" not in d["boundProps"]
    assert all(f["key"] != "city" for f in d["facets"])
    det = (await qa.get(f"{B}/object-types/store/objects/{d['items'][0]['id']}")).json()
    assert "city" not in det["values"] and "city" not in det["boundProps"]
    r = await qa.post(f"{B}/object-types/store/export", json={"props": ["city", "store_id"]})
    assert r.status_code == 200 and "城市" not in r.content.decode("utf-8-sig").split("\n")[0]
    r = await qa.post(f"{B}/object-types/store/export", json={})
    assert "城市" not in r.content.decode("utf-8-sig").split("\n")[0]


async def test_keyword_without_searchable_props_is_ignored(qa, users, monkeypatch):
    total = (await _search(qa, "store", size=1))["total"]
    monkeypatch.setattr(
        "app.modules.qa.explore_api.flags",
        lambda p: {"searchable": False, "filterable": False, "sortable": False},
    )
    # 不拿第一列兜底匹配：关键词被忽略
    assert (await _search(qa, "store", kw="不会命中的词xyz", size=1))["total"] == total


@pytest.mark.parametrize("operation", ["search", "export"])
@pytest.mark.parametrize("body", [{"kw": "bad\x00keyword"}, {"filters": {"city": ["bad\x00city"]}}])
async def test_nul_input_is_validation_error(qa, users, operation, body):
    await _open(users, "store", "city")
    r = await qa.post(f"{B}/object-types/store/{operation}", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "VALIDATION_FAILED"
    field = "kw" if "kw" in body else "filters.city"
    assert r.json()["details"]["fields"][0]["field"] == field


async def test_sort_accepts_only_one_descending_prefix(qa, users):
    await _set(users, "store", "city", sortable=True)
    assert (await qa.post(f"{B}/object-types/store/search", json={"sort": "-city"})).status_code == 200
    r = await qa.post(f"{B}/object-types/store/search", json={"sort": "--city"})
    assert r.status_code == 422, r.text
    assert r.json()["details"]["fields"][0]["field"] == "sort"
