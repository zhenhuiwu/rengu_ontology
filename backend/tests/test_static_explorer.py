"""通用对象浏览：属性标记 seed、指标口径变体、GET /object-types/{id}/meta 与 /facets（static_0004）。"""

import json
import re
from collections import Counter

import pytest
from sqlalchemy import select

from app.modules.biz.models import BizOrder, Store
from app.modules.static.explorer import default_flags, metric_variants
from app.modules.static.models import Metric, ObjectType, Property
from app.seeds.loader import run_seeds

P = "/api/ontology/v1"
REQUIRED = {"key", "name", "dataType", "searchable", "filterable", "sortable", "listed", "order"}
REQUIRED |= {"visibility", "format", "unit"}


@pytest.fixture
def static_sections():
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        yield BUILDERS


@pytest.fixture
async def seeded(session, static_sections):
    await run_seeds(session, ["admin", "versioning", "static", "biz"])
    await session.commit()
    return session


async def meta(client, oid: str):
    r = await client.get(f"{P}/object-types/{oid}/meta")
    assert r.status_code == 200, r.text
    return r.json()


async def facets(client, oid: str, filters=None, status: int = 200):
    params = {"filters": json.dumps(filters, ensure_ascii=False)} if filters is not None else {}
    r = await client.get(f"{P}/object-types/{oid}/facets", params=params)
    assert r.status_code == status, r.text
    return r.json()


def by_key(items):
    return {x["key"]: x for x in items}


# ---------------------------------------------------------------- 纯函数


def test_default_flags_generic_for_unseeded_type():
    f = default_flags("some_new_type", {"api": "x", "type": "enum", "pk": False, "dim": True})
    assert f["filterable"] and f["list_visible"] and not f["sortable"] and f["visibility"] == "normal"
    f = default_flags("some_new_type", {"api": "id", "type": "string", "pk": True})
    assert f["searchable"] and f["visibility"] == "prominent"
    f = default_flags("some_new_type", {"api": "amt", "type": "decimal"})
    assert f["sortable"] and not f["filterable"]


def test_metric_variants_by_name_structure():
    ms = [
        {"id": "a", "api": "a_net", "name": "金额（口径一）", "version": "v1"},
        {"id": "b", "api": "a_gross", "name": "金额（口径二）", "version": "v2"},
        {"id": "c", "api": "c", "name": "比率（%）", "version": "v1"},
        {"id": "d", "api": "d", "name": "计数", "version": "v1"},
    ]
    v = metric_variants(ms)
    assert set(v) == {"a", "b"}
    got = [(x["code"], x["label"], x["default"]) for x in v["a"]]
    assert got == [("a", "口径一", True), ("b", "口径二", False)]


# ---------------------------------------------------------------- seed


async def test_seed_metric_variants(seeded):
    rows = {m.code: m.variants for m in (await seeded.scalars(select(Metric))).all()}
    assert [x["label"] for x in rows["gmv"]] == ["不含税", "含税"]
    assert rows["gmv"] == rows["gmv_tax"]
    assert [x["code"] for x in rows["repeat_sales"]] == ["repeat_sales", "repeat_sales_tax"]
    assert rows["refund_rate"] == [] and rows["order_cnt"] == []


# ---------------------------------------------------------------- /meta


async def test_meta_store_matches_current_explorer(client, seeded):
    m = await meta(client, "store")
    assert m["titleProp"] == "store_name" and m["pkProp"] == "store_id"
    props = m["properties"]
    assert all(set(p) >= REQUIRED for p in props)
    assert [p["order"] for p in props] == sorted(p["order"] for p in props)
    assert [p["key"] for p in props if p["filterable"]] == ["format", "region_code", "city", "status"]
    listed = [p["key"] for p in props if p["listed"]]
    assert listed == ["store_id", "format", "region_code", "city", "area_sqm", "opened_at", "status"]
    p = by_key(props)
    assert p["area_sqm"]["unit"] == "㎡" and p["area_sqm"]["sortable"] and p["area_sqm"]["facetKind"] is None
    assert p["status"]["facetKind"] == "enum" and p["store_name"]["visibility"] == "prominent"
    assert m["kwPlaceholder"] == "门店编码 / 门店名称"
    assert m["hasData"] is True
    gmv = next(x for x in m["metrics"] if x["id"] == "gmv")
    assert [v["label"] for v in gmv["variants"]] == ["不含税", "含税"]


async def test_meta_other_types(client, seeded):
    prod = (await meta(client, "product"))["properties"]
    assert [p["key"] for p in prod if p["filterable"]] == ["category_id", "brand"]
    o = await meta(client, "order")
    assert [p["key"] for p in o["properties"] if p["filterable"]] == ["channel", "status"]
    mem = await meta(client, "member")  # 没登记 seed、没有业务表的类型：通用默认
    assert mem["hasData"] is False
    assert all(p["dataType"] in {"enum", "boolean"} for p in mem["properties"] if p["filterable"])


async def test_meta_unknown_type_404(client, seeded):
    r = await client.get(f"{P}/object-types/nope/meta")
    assert r.status_code == 404


# ---------------------------------------------------------------- /facets


async def test_facets_store_counts_from_data(client, seeded):
    stores = (await seeded.scalars(select(Store))).all()
    f = by_key(await facets(client, "store"))
    assert list(f) == ["format", "region_code", "city", "status"]
    region = {v["value"]: v["count"] for v in f["region_code"]["values"]}
    assert region == dict(Counter(s.region for s in stores))
    present = {s.status.value for s in stores}
    expect = [x for x in ["营业中", "装修中", "已闭店"] if x in present]
    assert [v["value"] for v in f["status"]["values"]] == expect
    assert sum(v["count"] for v in f["status"]["values"]) == len(stores)


async def test_facets_respect_other_filters_not_own(client, seeded):
    stores = (await seeded.scalars(select(Store))).all()
    city = Counter(s.city for s in stores).most_common(1)[0][0]
    f = by_key(await facets(client, "store", {"city": [city]}))
    assert {v["value"]: v["count"] for v in f["region_code"]["values"]} == dict(
        Counter(s.region for s in stores if s.city == city)
    )
    assert sum(v["count"] for v in f["city"]["values"]) == len(stores)  # 自己的条件不影响自己
    f = by_key(await facets(client, "store", {"status": ["营业中"]}))  # 枚举按中文标签过滤
    n_open = sum(1 for s in stores if s.status.value == "营业中")
    assert sum(v["count"] for v in f["format"]["values"]) == n_open


async def test_facets_order_and_invalid_props(client, seeded):
    orders = (await seeded.scalars(select(BizOrder))).all()
    f = by_key(await facets(client, "order", {"status": ["已取消"]}))
    n_cancel = sum(1 for x in orders if x.status.value == "已取消")
    assert sum(v["count"] for v in f["channel"]["values"]) == n_cancel
    await facets(client, "order", {"created_at": {"min": "2026-10-01"}}, status=422)  # 不可筛选
    await facets(client, "order", {"nope": ["x"]}, status=422)


async def test_facets_range_props(client, seeded):
    ot = await seeded.scalar(select(ObjectType).where(ObjectType.code == "store"))
    for api in ("area_sqm", "opened_at"):
        q = select(Property).where(Property.object_type_id == ot.id, Property.api_name == api)
        p = await seeded.scalar(q)
        p.filterable = True
    await seeded.commit()
    stores = (await seeded.scalars(select(Store))).all()
    f = by_key(await facets(client, "store"))
    areas = [float(s.area) for s in stores if s.area is not None]
    assert f["area_sqm"]["kind"] == "range"
    assert (f["area_sqm"]["min"], f["area_sqm"]["max"]) == (min(areas), max(areas))
    assert f["opened_at"]["min"] == min(s.opened_at for s in stores).isoformat()
    f = by_key(await facets(client, "store", {"area_sqm": {"min": 100}, "opened_at": {"max": "2030-01-01"}}))
    assert sum(v["count"] for v in f["status"]["values"]) == sum(1 for a in areas if a >= 100)


async def test_facets_type_without_data(client, seeded):
    f = await facets(client, "member")
    assert f and all(x["hasData"] is False for x in f)
    assert all(x.get("values") == [] or x.get("min") is None for x in f)


async def test_facets_bad_filters_422(client, seeded):
    await facets(client, "store", None)
    r = await client.get(f"{P}/object-types/store/facets", params={"filters": "not json"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    r = await client.get(f"{P}/object-types/store/facets", params={"filters": "[1]"})
    assert r.status_code == 422


# ---------------------------------------------------------------- 筛选值严格校验（评审阻断）


async def _make_filterable(session, obj: str, *apis: str, **extra):
    ot = await session.scalar(select(ObjectType).where(ObjectType.code == obj))
    for api in apis:
        q = select(Property).where(Property.object_type_id == ot.id, Property.api_name == api)
        p = await session.scalar(q)
        p.filterable = True
        for k, v in extra.items():
            setattr(p, k, v)
    await session.commit()


BAD_FILTERS = [
    {"status": ["不存在"]},  # 枚举取值不存在
    {"status": "营业中"},  # 枚举类只收数组
    {"city": 5},
    {"city": [1]},
    {"city": [True]},
    {"city": [None]},
    {"city": [["上海"]]},
    {"city": [{"a": 1}]},
    {"city": {"min": "a"}},  # 字符串属性不收区间
    {"status": {"min": "营业中"}},
    {"opened_at": {"min": 5}},
    {"opened_at": {"min": "not-a-date"}},
    {"opened_at": ["2026-01-01"]},  # 区间类只收 {min,max}
    {"opened_at": "2026-01-01"},
    {"opened_at": {}},
    {"opened_at": {"from": "2026-01-01"}},
    {"area_sqm": {"min": True}},
    {"area_sqm": {"min": [1]}},
    {"area_sqm": {"min": {"x": 1}}},
    {"area_sqm": {"min": "abc"}},
    {"area_sqm": {"max": 10**400}},
    {"area_sqm": {"max": "1e400"}},
    {"area_sqm": {"max": 1e300 * 1e300}},  # json.dumps → Infinity
]


@pytest.mark.parametrize("flt", BAD_FILTERS, ids=lambda f: json.dumps(f, ensure_ascii=False)[:40])
async def test_facets_bad_filter_values_422(client, seeded, flt):
    await _make_filterable(seeded, "store", "area_sqm", "opened_at")
    r = await client.get(f"{P}/object-types/store/facets", params={"filters": json.dumps(flt)})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["details"]["fields"][0]["field"] == f"filters.{next(iter(flt))}"
    msg = body["details"]["fields"][0]["message"]
    assert re.search(r"[\u4e00-\u9fff]", msg) and "Invalid" not in msg and "could not" not in msg, msg


NUL_FILTERS = [
    {"status": ["营业\x00中"]},  # 枚举
    {"city": ["上\x00海"]},  # 字符串枚举
    {"opened_at": {"min": "2026-01-01\x00"}},  # 日期区间
    {"area_sqm": {"min": "1\x000"}},  # 数值区间（字符串）
]


@pytest.mark.parametrize("flt", NUL_FILTERS, ids=["enum", "str", "date", "num"])
async def test_facets_nul_in_filter_value_422(client, seeded, flt):
    await _make_filterable(seeded, "store", "area_sqm", "opened_at")
    r = await client.get(f"{P}/object-types/store/facets", params={"filters": json.dumps(flt)})
    assert r.status_code == 422, r.text
    f = r.json()["details"]["fields"][0]
    assert r.json()["code"] == "VALIDATION_FAILED" and f["field"] == f"filters.{next(iter(flt))}"
    assert "NUL" in f["message"]


async def test_facets_good_filter_values_still_work(client, seeded):
    await _make_filterable(seeded, "store", "area_sqm", "opened_at")
    for flt in (
        {"status": ["open", "已闭店"]},
        {"area_sqm": {"min": "100", "max": 99999}},
        {"opened_at": {"min": "2000-01-01", "max": None}},
        {"city": []},
    ):
        await facets(client, "store", flt)


async def test_huge_int_on_integer_column_422(client, seeded):
    # 演示业务表没有可筛选的整数列，直接测列类型校验
    from sqlalchemy import Column, Integer

    from app.core.errors import AppError
    from app.modules.static.explorer import _scalar

    col = Column("n", Integer)
    assert _scalar(col, 5, "n") == 5
    for bad in (2**31, -(2**31) - 1, 1.5, "x", True):
        with pytest.raises(AppError):
            _scalar(col, bad, "n")


# ---------------------------------------------------------------- 敏感 / 隐藏属性不出分面


def test_default_flags_sensitive_and_hidden_not_filterable():
    f = default_flags("some_new_type", {"api": "x", "type": "enum", "sensitive": True})
    assert f["filterable"] is False
    f = default_flags("store", {"api": "city", "type": "string", "sensitive": True})  # seed 里开了也不行
    assert f["filterable"] is False


async def test_seed_sensitive_props_not_filterable(seeded):
    rows = (await seeded.scalars(select(Property).where(Property.is_sensitive.is_(True)))).all()
    assert rows and not any(p.filterable for p in rows)


async def test_sensitive_prop_excluded_from_facets(client, seeded):
    await _make_filterable(seeded, "store", "city", is_sensitive=True)
    keys = [x["key"] for x in await facets(client, "store")]
    assert "city" not in keys and "region_code" in keys
    meta_city = by_key((await meta(client, "store"))["properties"])["city"]
    assert meta_city["filterable"] is False and meta_city["facetKind"] is None
    await facets(client, "store", {"city": ["上海"]}, status=422)


async def test_hidden_prop_excluded_from_facets(client, seeded):
    await _make_filterable(seeded, "store", "city", visibility="hidden")
    keys = [x["key"] for x in await facets(client, "store")]
    assert "city" not in keys and "region_code" in keys
    assert "city" not in by_key((await meta(client, "store"))["properties"])
    await facets(client, "store", {"city": ["上海"]}, status=422)


# ---------------------------------------------------------------- 行级范围


async def test_facets_apply_row_filter(client, seeded, monkeypatch):
    import app.common.auth as auth

    stores = (await seeded.scalars(select(Store))).all()
    city = Counter(s.city for s in stores).most_common(1)[0][0]

    async def fake_row_filter(user, object_type):
        return {"field": "city", "op": "=", "value": city} if object_type == "store" else None

    monkeypatch.setattr(auth, "row_filter", fake_row_filter)
    f = by_key(await facets(client, "store"))
    assert [v["value"] for v in f["city"]["values"]] == [city]
    assert sum(v["count"] for v in f["status"]["values"]) == sum(1 for s in stores if s.city == city)
