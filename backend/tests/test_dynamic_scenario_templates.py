"""情景推演模板：/scenarios/meta 下发模板定义；方案带模板编号；范围 / 驱动按模板字段校验；引擎按模板插拔。"""

import json

from app.core.config import get_settings
from app.modules.dynamic import scenario_templates as st
from tests.dynamic_support import API, dyn, err, fakes  # noqa: F401
from tests.test_dynamic_scenarios_lifecycles import RUN


def _tpl():
    return json.loads(
        (get_settings().seeds_json_dir / "dynamic/SC_TEMPLATES.json").read_text(encoding="utf-8")
    )


def _js(name: str):
    return json.loads((get_settings().seeds_json_dir / f"dynamic/{name}.json").read_text(encoding="utf-8"))


def test_demo_template_matches_catalog():
    t = st.template_of(None)
    assert t["code"] == "demo_retail" and t["engine"] in st.ENGINES
    opts = {f["key"]: f.get("options") for f in t["scope"] + st.driver_fields(t)}
    assert opts["area"] == _js("SC_AREAS") and opts["category"] == _js("SC_CATEGORIES")
    assert opts["promo"] == _js("PROMO_TYPES")
    assert st.defaults(st.driver_fields(t)) == {
        "pricePct": 0,
        "replenishPct": 0,
        "safetyDays": 7,
        "promo": "无",
        "promoDepth": 0,
        "promoWeeks": 0,
    }
    assert [s["action"] for s in t["suggestions"]] == ["发起调价审批", "补货申请", "发放复购召回优惠券"]


async def test_meta_and_template_code(dyn, client):  # noqa: F811
    meta = (await client.get(f"{API}/scenarios/meta")).json()
    assert meta["templates"] == _tpl()
    d = (await client.get(f"{API}/scenarios/SCN-0018")).json()
    assert d["templateCode"] == "demo_retail"
    new = (await client.post(f"{API}/scenarios", json={})).json()
    assert new["templateCode"] == "demo_retail" and new["drivers"]["safetyDays"] == 7


async def test_run_validates_against_template(dyn, client, fakes):  # noqa: F811
    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    cases = [
        ({**RUN, "scope": {**RUN["scope"], "weeks": 99}}, "scope.weeks"),
        ({**RUN, "scope": {**RUN["scope"], "planet": "火星"}}, "scope.planet"),
        ({**RUN, "drivers": {**RUN["drivers"], "pricePct": "x"}}, "drivers.pricePct"),
        ({**RUN, "drivers": {**RUN["drivers"], "promo": "买一送一"}}, "drivers.promo"),
    ]
    for body, loc in cases:
        r = await client.post(f"{API}/scenarios/{sid}:run", json=body)
        err(r, 422, "VALIDATION_FAILED")
        assert loc in r.text
    # 促销类型为“无”时力度 / 周数按模板 offValue 归零
    body = {**RUN, "drivers": {**RUN["drivers"], "promoDepth": 9, "promoWeeks": 3}}
    r = await client.post(f"{API}/scenarios/{sid}:run", json=body)
    assert (
        r.status_code == 202
        and r.json()["drivers"]["promoDepth"] == 0
        and r.json()["drivers"]["promoWeeks"] == 0
    )


async def test_scope_options_from_facets(dyn, client, fakes, monkeypatch):  # noqa: F811
    """范围项 propRef：分面有数据 → 可选值 = allOption + 分面取值 + 模板固定项；无数据 → 只用模板 options。"""
    from app.modules.static.explorer import ExplorerService

    facets = {
        "store": [
            {"key": "region_code", "kind": "enum", "hasData": True, "values": [{"value": "EC", "count": 3}]}
        ],
        "product": [{"key": "category_id", "kind": "enum", "hasData": False, "values": []}],
    }

    async def fake(self, code, filters, user):
        return facets[code]

    monkeypatch.setattr(ExplorerService, "facets", fake)
    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    ok = {**RUN, "scope": {**RUN["scope"], "area": "EC"}}
    assert (await client.post(f"{API}/scenarios/{sid}:run", json=ok)).status_code == 202
    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    bad = {**RUN, "scope": {**RUN["scope"], "area": "ZZ"}}
    r = await client.post(f"{API}/scenarios/{sid}:run", json=bad)
    err(r, 422, "VALIDATION_FAILED")
    assert "scope.area" in r.text
    cat = {**RUN, "scope": {**RUN["scope"], "area": _js("SC_AREAS")[1], "category": _js("SC_CATEGORIES")[1]}}
    assert (await client.post(f"{API}/scenarios/{sid}:run", json=cat)).status_code == 202
