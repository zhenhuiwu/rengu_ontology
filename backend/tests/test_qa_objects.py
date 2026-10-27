"""Legacy object DTOs delegate to published object projections and current authorized rows."""

import pytest

from app.modules.qa.objects_api import fmt_money, fmt_pct
from tests.qa_access_support import seed_qa_access
from tests.use_objects_support import enable_fixture_export

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await enable_fixture_export(users)
    return client


def test_js_ports():
    assert fmt_money(1234.5) == "1,234.50" and fmt_money(0.125) == "0.13" and fmt_money(None) == "--"
    assert fmt_pct(0.857142) == "85.71%"


async def test_search_store_facets_and_paging(qa):
    response = await qa.post(f"{B}/objects/store/search", json={"size": 16})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 95 and len(data["items"]) == 16
    assert data["scopeText"] == "仅含当前授权实例"
    assert {"store_id", "store_name"} <= set(data["items"][0]["cells"])
    assert "sales" not in data["items"][0]["cells"]
    facet = next(f for f in data["facets"] if f["key"] == "format")
    assert sum(v["n"] for v in facet["vals"]) == data["total"]
    chosen = facet["vals"][0]
    filtered = (await qa.post(f"{B}/objects/store/search", json={"facets": {"format": [chosen["v"]]}})).json()
    assert filtered["total"] == chosen["n"]
    assert next(f for f in filtered["facets"] if f["key"] == "format")["vals"] == facet["vals"]
    assert (
        await qa.post(f"{B}/objects/store/search", json={"facets": {"region": ["华东一区"]}})
    ).status_code == 422
    assert (
        await qa.post(f"{B}/objects/store/search", json={"filters": {"format": [chosen["v"]]}})
    ).status_code == 422


async def test_search_order_and_product(qa):
    bad_alias = await qa.post(f"{B}/objects/order/search", json={"facets": {"ostatus": ["已取消"]}})
    assert bad_alias.status_code == 422
    response = await qa.post(f"{B}/objects/order/search", json={"facets": {"status": ["已取消"]}})
    assert response.status_code == 200, response.text
    assert response.json()["total"] > 0
    assert all(row["cells"]["status"] == "已取消" for row in response.json()["items"])
    product = (await qa.post(f"{B}/objects/product/search", json={})).json()
    assert product["total"] == 12 and {"category_id", "brand"} <= {f["key"] for f in product["facets"]}
    assert (await qa.post(f"{B}/objects/nope/search", json={})).status_code == 404


async def test_export_csv(qa):
    response = await qa.post(f"{B}/objects/store/export", json={"facets": {"format": ["社区店"]}})
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "门店名称" in response.content.decode("utf-8-sig").splitlines()[0]
    assert "销售" not in response.content.decode("utf-8-sig").splitlines()[0]
    assert (await qa.post(f"{B}/objects/store/export", json={"ids": []})).content.decode("utf-8-sig").count(
        "\n"
    ) == 1
    assert (await qa.post(f"{B}/objects/store/export?ver=v2026.10.2", json={})).status_code == 200
    assert (await qa.post(f"{B}/objects/store/export?ver=missing", json={})).status_code == 404
    assert (await qa.post(f"{B}/objects/store/export?previewAs=U010763", json={})).status_code == 403


async def test_scoped_ask(qa):
    body = {"scope": {"kind": "store", "filter": {"facets": {"format": ["社区店"]}}}, "question": "合计"}
    assert (await qa.post(f"{B}/scoped-ask", json=body)).status_code == 503
    body["scope"]["filter"]["facets"] = {"region": ["华东四区"]}
    assert (await qa.post(f"{B}/scoped-ask", json=body)).status_code == 422
    explicit = {"scope": {"kind": "order", "ids": ["SO202610250794580"]}, "question": "paid_amount合计"}
    response = await qa.post(f"{B}/scoped-ask", json=explicit)
    assert response.status_code == 200, response.text
    assert "274.5" in response.json()["text"] and "未套用指标口径" in response.json()["basis"]
    assert (await qa.post(f"{B}/scoped-ask?ver=v2026.10.2", json=explicit)).status_code == 403


async def test_detail_store_and_drawer(qa):
    response = await qa.get(f"{B}/objects/store/SH0031")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["kindLabel"] == "门店" and data["id"] == "SH0031" and data["dataVersion"] == "current"
    assert data["kpis"] == [] and not data["analyticsConfigured"]
    assert "店长" not in {field["label"] for field in data["fields"]}
    drawer = (await qa.get(f"{B}/objects/store/SH0031?view=drawer")).json()
    assert "kpis" not in drawer and drawer["groups"] == data["groups"]
    assert (await qa.get(f"{B}/objects/store/NOPE")).status_code == 404
    assert (await qa.get(f"{B}/objects/region/REG-EC")).status_code == 404
    order = (await qa.get(f"{B}/objects/order/SO202610250794580")).json()
    assert order["code"] == "SO202610250794580" and "status" not in order
    assert (await qa.get(f"{B}/objects/product/1002387")).json()["title"] == "农夫山泉饮用天然水"


async def test_related_timeline_actions(qa):
    related = (await qa.get(f"{B}/objects/store/SH0031/related")).json()
    assert "order_belongs_to_store" in {group["key"] for group in related}
    assert "peer" not in {group["key"] for group in related}
    assert (await qa.get(f"{B}/objects/store/SH0031/related?group=peer")).status_code == 404
    timeline = (await qa.get(f"{B}/objects/store/SH0031/timeline")).json()
    assert timeline["total"] == len(timeline["items"]) == 1 and timeline["configured"]
    assert timeline["items"][0]["text"] == "开业日期"
    assert (await qa.get(f"{B}/objects/product/1002387/timeline")).json()["items"] == []
    assert (await qa.get(f"{B}/objects/order/SO202610250794580/actions")).json() == []
