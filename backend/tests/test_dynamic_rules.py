"""规则与自动化：列表 / 详情 / 命中 / 回放 / 版本 / 保存草稿（dynamic-api.md §4.3、§6.3）。"""

import json

from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.dynamic.rules_models import (
    Lifecycle,
    LifecycleChange,
    LifecycleState,
    LifecycleTransition,
    LifecycleVersion,
    Rule,
    RuleHit,
    RuleVersion,
    Scenario,
    ScenarioImpact,
    ScenarioResult,
)
from app.modules.dynamic.rules_service import CUT_30D
from tests.dynamic_support import API, as_, dyn, err, fakes  # noqa: F401


def _json(rel: str):
    return json.loads((get_settings().seeds_json_dir / rel).read_text(encoding="utf-8"))


def _derived(name: str, rid: str):
    for x in _json(f"dynamic/derived/{name}.json"):
        a = x["args"][0]
        if (a.get("id") if isinstance(a, dict) else a) == rid:
            return x["result"]
    raise KeyError(rid)


async def test_seed_counts(dyn):  # noqa: F811
    c = lambda m: dyn.scalar(select(func.count()).select_from(m))  # noqa: E731
    assert await c(Rule) == 16
    assert await c(RuleVersion) == 28
    assert await c(RuleHit) == 1186
    assert await c(Scenario) == 18
    assert await c(ScenarioResult) == 15
    assert await c(ScenarioImpact) == 341
    assert await c(Lifecycle) == 6
    assert await c(LifecycleVersion) == 14
    assert await c(LifecycleState) == 28
    assert await c(LifecycleTransition) == 35
    assert await c(LifecycleChange) == 246


async def test_list_and_filters(dyn, client):  # noqa: F811
    r = (await client.get(f"{API}/rules")).json()
    assert r["total"] == 16 and len(r["items"]) == 16
    assert [x["id"] for x in r["items"]] == [x["id"] for x in _json("dynamic/RULE_DEFS.json")]
    ev = (await client.get(f"{API}/rules", params={"trigger": "事件"})).json()
    assert ev["total"] > 0 and all(x["trigger"]["kind"] == "事件" for x in ev["items"])
    kw = (await client.get(f"{API}/rules", params={"kw": "毛利率"})).json()
    assert "R-007" in [x["id"] for x in kw["items"]]
    meta = (await client.get(f"{API}/rules/meta")).json()
    assert meta["confirmers"] == _json("dynamic/CONFIRMERS.json")
    assert meta["mappingSources"] == _json("dynamic/MAPPING_SOURCES.json")
    err(await client.get(f"{API}/rules/R-NOPE"), 404, "NOT_FOUND")


async def test_detail_draft_versions(dyn, client):  # noqa: F811
    d = (await client.get(f"{API}/rules/R-007", params={"view": "draft"})).json()
    assert d["displayStatus"] == "草稿中" and d["cr"] == "CR-0011" and d["editable"] is True
    assert d["draft"]["version"] == 4 and d["version"] == 3
    v = (await client.get(f"{API}/rules/R-007/versions")).json()
    assert v[0]["status"] == "草稿" and v[0]["cr"] == "CR-0011"
    assert v[1:] == _derived("ruleVersions", "R-007")
    plain = (await client.get(f"{API}/rules/R-012")).json()
    assert plain["draft"] is None and plain["displayStatus"] == "已发布"


async def test_hits_paging_and_detail(dyn, client):  # noqa: F811
    want = _derived("hitsOf", "R-007")
    p1 = (await client.get(f"{API}/rules/R-007/hits", params={"range": "30d"})).json()
    assert p1["size"] == 12 and len(p1["items"]) == 12
    all_ = (await client.get(f"{API}/rules/R-007/hits", params={"range": "30d", "size": 200})).json()
    assert [x["id"] for x in all_["items"]] == [x["id"] for x in want if x["at"] >= CUT_30D]
    assert all_["total"] == p1["total"]
    err(await client.get(f"{API}/rules/R-007/hits", params={"range": "1y"}), 422, "VALIDATION_FAILED")
    dedup = next(x for x in want if x["result"] == "已去重")
    h = (await client.get(f"{API}/rule-hits/{dedup['id']}")).json()
    assert h["alert"] is None and len(h["timeline"]) == 2
    assert "冷却期" in h["timeline"][1]["text"]
    alert_hit = next(x for x in want if x["alertId"])
    h = (await client.get(f"{API}/rule-hits/{alert_hit['id']}")).json()
    assert h["alert"]["id"] == alert_hit["alertId"] and h["timeline"]
    err(await client.get(f"{API}/rule-hits/HIT-NOPE"), 404, "NOT_FOUND")


async def test_replay_is_deterministic_and_writes_nothing(dyn, client):  # noqa: F811
    before = await dyn.scalar(select(func.count()).select_from(RuleHit))
    body = {"range": "7d", "version": "draft"}
    a = (await client.post(f"{API}/rules/R-007:replay", json=body)).json()
    b = (await client.post(f"{API}/rules/R-007:replay", json=body)).json()
    assert a == b and a["actual"] == 3 and a["would"] == 5 and a["added"] == 2
    assert await dyn.scalar(select(func.count()).select_from(RuleHit)) == before
    cur = (await client.post(f"{API}/rules/R-012:replay", json={"range": "30d", "version": "current"})).json()
    assert cur["added"] == 0 and cur["removed"] == 0
    err(await client.post(f"{API}/rules/R-007:replay", json={"range": "1y"}), 422, "VALIDATION_FAILED")


async def test_save_draft(dyn, client):  # noqa: F811
    url = f"{API}/rules/R-007/draft"
    d = (await client.get(f"{API}/rules/R-007", params={"view": "draft"})).json()["draft"]
    missing = {
        "cr": "CR-0011",
        "autoStage": {**d["autoStage"], "actionTypeId": "request_replenish", "mapping": []},
    }
    body = err(await client.put(url, json=missing), 422, "STAGE_MAPPING_MISSING")
    assert "商品" in body["details"]["missing"]
    bad = {"cr": "CR-0011", "autoStage": {**d["autoStage"], "confirmer": "不存在的人"}}
    err(await client.put(url, json=bad), 422, "VALIDATION_FAILED")
    ok = {"cr": "CR-0011", "autoStage": {**d["autoStage"], "enabled": False}}
    r = await client.put(url, json=ok)
    assert r.status_code == 200, r.text
    again = (await client.get(f"{API}/rules/R-007", params={"view": "draft"})).json()
    assert again["draft"]["autoStage"]["enabled"] is False
    assert again["autoStage"]["enabled"] is True  # 已发布定义不动
    # 没有草稿的规则：已发布只读
    err(await client.put(f"{API}/rules/R-012/draft", json=ok), 409, "STATE_INVALID")
