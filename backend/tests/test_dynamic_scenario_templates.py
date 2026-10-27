"""情景推演模板：/scenarios/meta 下发模板定义；方案带模板编号；范围 / 驱动按模板字段校验；引擎按模板插拔。"""

import json

import pytest

from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.dynamic import scenario_templates as st
from tests.dynamic_support import API, dyn, err, fakes  # noqa: F401
from tests.test_dynamic_scenarios_lifecycles import RUN


def _tpl():
    return json.loads(
        (get_settings().seeds_json_dir / "dynamic/SC_TEMPLATES.json").read_text(encoding="utf-8")
    )


def _js(name: str):
    return json.loads((get_settings().seeds_json_dir / f"dynamic/{name}.json").read_text(encoding="utf-8"))


async def test_demo_template_matches_catalog(dyn):  # noqa: F811
    t = await st.template_of(dyn, None)
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


async def test_templates_are_per_ontology(dyn, client, conn):  # noqa: F811
    """dynamic_0024：模板来自 scenario_template（空间 1 由迁移导入 SC_TEMPLATES）；新空间没有模板、不回落。"""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.ontology import set_ontology_scope
    from app.modules.ontology.models import OntologyWorkspace

    r = (await client.get(f"{API}/scenarios/templates")).json()
    assert r["items"] == _tpl()
    assert [t["code"] for t in await st.templates(dyn)] == [t["code"] for t in _tpl()]
    ws = OntologyWorkspace(code="dyn-tpl-empty", name="dyn tpl empty")
    dyn.add(ws)
    await dyn.flush()
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as other:
        await set_ontology_scope(other, ws.id)
        assert await st.templates(other) == []
        with pytest.raises(AppError) as e:
            await st.template_of(other, None)
        assert e.value.code == "SCENARIO_EXECUTOR_UNAVAILABLE" and e.value.status_code == 503


async def test_meta_and_template_code(dyn, client):  # noqa: F811
    meta = (await client.get(f"{API}/scenarios/meta")).json()
    assert [t["code"] for t in meta["templates"]] == [t["code"] for t in _tpl()]
    assert meta["executorConfigured"] is False
    assert next(f for f in meta["templates"][0]["scope"] if f["key"] == "area")["options"] == meta["areas"]
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
        ({**RUN, "drivers": {**RUN["drivers"], "pricePct": float("nan")}}, "drivers.pricePct"),
        ({**RUN, "drivers": {**RUN["drivers"], "replenishPct": float("inf")}}, "drivers.replenishPct"),
        ({**RUN, "drivers": {**RUN["drivers"], "safetyDays": True}}, "drivers.safetyDays"),
        ({**RUN, "scope": {**RUN["scope"], "weeks": float("nan")}}, "scope.weeks"),
        ({**RUN, "scope": {**RUN["scope"], "weeks": 2.5}}, "scope.weeks"),
        ({**RUN, "drivers": {**RUN["drivers"], "promoWeeks": 1.5}}, "drivers.promoWeeks"),
    ]
    for body, loc in cases:
        # NaN / Infinity 按非标准 JSON 字面量原样发（json.dumps 默认允许）
        raw = json.dumps(body)
        r = await client.post(
            f"{API}/scenarios/{sid}:run", content=raw, headers={"content-type": "application/json"}
        )
        err(r, 422, "VALIDATION_FAILED")
        assert loc in r.text
    # 合法配置仍不能伪造真实推演：缺执行器503，原配置保持。
    before = (await client.get(f"{API}/scenarios/{sid}")).json()
    r = await client.post(f"{API}/scenarios/{sid}:run", json={**RUN, "scope": {**RUN["scope"], "weeks": 8.0}})
    err(r, 503, "SCENARIO_EXECUTOR_UNAVAILABLE")
    assert (await client.get(f"{API}/scenarios/{sid}")).json() == before


async def test_scope_options_from_facets(dyn, client, fakes, monkeypatch):  # noqa: F811
    """引擎 accepts_facet_values：分面有数据 → 可选值 = allOption + 分面取值；无数据 → 模板 options。
    演示引擎为 False：不取分面，只认模板 options。"""
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
    code = _js("SC_AREAS")[1]
    ec = {**RUN, "scope": {**RUN["scope"], "area": "EC"}}
    # 演示引擎：分面值不接受，模板固定项照常
    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    err(await client.post(f"{API}/scenarios/{sid}:run", json=ec), 422, "VALIDATION_FAILED")
    ok = {**RUN, "scope": {**RUN["scope"], "area": code}}
    assert (await client.post(f"{API}/scenarios/{sid}:run", json=ok)).status_code == 503
    # 能按分面值计算的引擎（替身）：可选值改为分面取值
    monkeypatch.setitem(
        st.ENGINES, "demo_retail", {**st.ENGINES["demo_retail"], "accepts_facet_values": True}
    )
    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    assert (await client.post(f"{API}/scenarios/{sid}:run", json=ec)).status_code == 503
    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    r = await client.post(f"{API}/scenarios/{sid}:run", json=ok)
    err(r, 422, "VALIDATION_FAILED")
    assert "scope.area" in r.text
    cat = {**RUN, "scope": {**RUN["scope"], "area": "华东大区全部", "category": _js("SC_CATEGORIES")[1]}}
    assert (await client.post(f"{API}/scenarios/{sid}:run", json=cat)).status_code == 503
