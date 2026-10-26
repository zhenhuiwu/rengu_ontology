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
    RuleDraft,
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
    assert r["total"] == 16 and r["size"] == 10 and len(r["items"]) == 10
    assert [x["id"] for x in r["items"]] == [x["id"] for x in _json("dynamic/RULE_DEFS.json")][:10]
    ev = (await client.get(f"{API}/rules", params={"trigger": "事件"})).json()
    assert ev["total"] > 0 and all(x["trigger"]["kind"] == "事件" for x in ev["items"])
    kw = (await client.get(f"{API}/rules", params={"kw": "毛利率"})).json()
    assert "R-007" in [x["id"] for x in kw["items"]]
    meta = (await client.get(f"{API}/rules/meta")).json()
    assert "王敏" in meta["confirmers"] and "定价委员会" not in meta["confirmers"]
    assert "固定值：仓库调拨" in meta["mappingSources"]  # 真实已存R-012配置，不能被旧常量误拒绝。
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
    assert p1["size"] == 10 and len(p1["items"]) == 10
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
    err(await client.post(f"{API}/rules/R-007:replay", json=body), 503, "RULE_EVALUATOR_UNAVAILABLE")
    err(await client.post(f"{API}/rules/R-007:replay", json=body), 503, "RULE_EVALUATOR_UNAVAILABLE")
    assert await dyn.scalar(select(func.count()).select_from(RuleHit)) == before
    err(
        await client.post(f"{API}/rules/R-012:replay", json={"range": "30d", "version": "current"}),
        503,
        "RULE_EVALUATOR_UNAVAILABLE",
    )
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


async def test_create_open_discard_rule(dyn, client):  # noqa: F811
    r = await client.post(f"{API}/rules", json={"cr": "CR-0099", "name": "新规则 A"})
    assert r.status_code == 201
    d = r.json()
    rid = d["id"]
    assert d["version"] is None and d["cr"] == "CR-0099"
    assert d["draft"]["version"] == 1 and d["editable"] is True
    assert d["displayStatus"] == "草稿中" and d["name"] == "新规则 A"
    lst = (await client.get(f"{API}/rules", params={"size": 100})).json()
    assert rid in [x["id"] for x in lst["items"]]  # 未发布但有草稿的新规则也列出
    # 已在别的草稿单 → 409；同单幂等
    err(await client.post(f"{API}/rules/{rid}/draft", json={"cr": "CR-0100"}), 409, "TARGET_IN_OTHER_CR")
    assert (await client.post(f"{API}/rules/{rid}/draft", json={"cr": "CR-0099"})).status_code == 200
    # 放弃：新规则连同规则删除
    assert (await client.delete(f"{API}/rules/{rid}/draft", params={"cr": "CR-0099"})).status_code == 204
    err(await client.get(f"{API}/rules/{rid}"), 404, "NOT_FOUND")
    # 不带 cr：只建规则，再开草稿
    rid2 = (await client.post(f"{API}/rules", json={})).json()["id"]
    assert (await client.post(f"{API}/rules/{rid2}/draft", json={"cr": "CR-0101"})).json()["cr"] == "CR-0101"
    err(await client.delete(f"{API}/rules/{rid2}/draft", params={"cr": "CR-0102"}), 404, "NOT_FOUND")


async def test_disabled_rule_open_discard_stays_disabled(dyn, client):  # noqa: F811
    assert (await client.get(f"{API}/rules/R-025")).json()["status"] == "已停用"
    assert (await client.post(f"{API}/rules/R-025/draft", json={"cr": "CR-0099"})).status_code == 200
    assert (await client.delete(f"{API}/rules/R-025/draft", params={"cr": "CR-0099"})).status_code == 204
    assert (await client.get(f"{API}/rules/R-025")).json()["status"] == "已停用"  # 不被误改成已发布


async def _cr(client, title: str) -> str:
    r = await client.post(f"{API}/change-requests", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _items(client, cr: str, kind: str, tid: str):
    r = await client.get(f"{API}/change-requests/{cr}/items", params={"kind": kind, "targetId": tid})
    assert r.status_code == 200, r.text
    return r.json()


async def test_rule_draft_registers_cr_items(dyn, client):  # noqa: F811
    """规则草稿登记到变更单（cr_target + cr_items，同一事务）。

    新建 / 保存（带 lockVersion）/ 提交不再 CR_EMPTY / 放弃不留孤儿。
    """
    cr = await _cr(client, "规则闭环")
    rid = (await client.post(f"{API}/rules", json={"cr": cr, "name": "新规则 B"})).json()["id"]
    one = (await client.get(f"{API}/change-requests/{cr}")).json()
    assert {"kind": "rule", "id": rid, "isNew": True, "name": "新规则 B"} in one["targets"]
    rows = await _items(client, cr, "rule", rid)
    assert rows and {x["section"] for x in rows} == {"config"} and all(x["op"] == "add" for x in rows)
    d = (await client.get(f"{API}/rules/{rid}", params={"view": "draft", "cr": cr})).json()
    lock = (await client.get(f"{API}/change-requests/{cr}")).json()["lockVersion"]
    body = {"cr": cr, "autoStage": {**d["draft"]["autoStage"], "enabled": False}, "lockVersion": lock}
    r = await client.put(f"{API}/rules/{rid}/draft", json=body)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["lockVersion"] > lock and saved["crChanges"] >= 1 and saved["draft"]
    err(await client.put(f"{API}/rules/{rid}/draft", json=body), 409, "VERSION_CONFLICT")  # 旧 lockVersion
    r = await client.post(f"{API}/change-requests/{cr}:submit")
    assert r.status_code == 200 or r.json()["code"] != "CR_EMPTY", r.text

    # 已发布规则发起变更：登记目标；保存草稿 → autoStage 改动行；放弃 → 目标与行一并删除，规则草稿删除
    cr2 = await _cr(client, "规则变更")
    assert (await client.post(f"{API}/rules/R-015/draft", json={"cr": cr2})).status_code == 200
    d = (await client.get(f"{API}/rules/R-015", params={"view": "draft", "cr": cr2})).json()
    chg = {
        "cr": cr2,
        "autoStage": {**d["draft"]["autoStage"], "enabled": not d["draft"]["autoStage"]["enabled"]},
    }
    r = await client.put(f"{API}/rules/R-015/draft", json=chg)
    assert r.status_code == 200, r.text
    rows = await _items(client, cr2, "rule", "R-015")
    assert [(x["section"], x["key"], x["op"]) for x in rows] == [("config", "autoStage.enabled", "update")]
    assert (await client.delete(f"{API}/rules/R-015/draft", params={"cr": cr2})).status_code == 204
    assert await _items(client, cr2, "rule", "R-015") == []
    tg = (await client.get(f"{API}/change-requests/{cr2}")).json()["targets"]
    assert all(t["id"] != "R-015" for t in tg)


async def test_hits_use_generic_dims(dyn, client):  # noqa: F811
    items = (await client.get(f"{API}/rules/R-007/hits", params={"range": "30d", "size": 200})).json()[
        "items"
    ]
    assert items and all("dims" in x and "storeId" not in x for x in items)
    assert any(x["dims"] and x["dims"][0]["objKind"] == "store" for x in items)


async def test_apply_cr_promotes_rule_drafts(dyn, client):  # noqa: F811
    """发布写回 apply_cr：新规则 → v1 已发布；已发布 → version+1；停用规则保持停用；重复调用无副作用。"""
    from app.modules.dynamic.writeback import apply_cr

    cr = await _cr(client, "发布写回")
    rid = (await client.post(f"{API}/rules", json={"cr": cr, "name": "新规则 W"})).json()["id"]
    old15 = (await client.get(f"{API}/rules/R-015")).json()
    assert (await client.post(f"{API}/rules/R-015/draft", json={"cr": cr})).status_code == 200
    d = (await client.get(f"{API}/rules/R-015", params={"view": "draft", "cr": cr})).json()["draft"]
    flip = not d["autoStage"]["enabled"]
    r = await client.put(
        f"{API}/rules/R-015/draft", json={"cr": cr, "autoStage": {**d["autoStage"], "enabled": flip}}
    )
    assert r.status_code == 200, r.text
    old25 = (await client.get(f"{API}/rules/R-025")).json()
    assert (await client.post(f"{API}/rules/R-025/draft", json={"cr": cr})).status_code == 200

    out = await apply_cr(dyn, cr, by="U010001")
    assert sorted(out["rules"]) == sorted([rid, "R-015", "R-025"]) and out["lifecycles"] == []
    new = (await client.get(f"{API}/rules/{rid}")).json()
    assert new["version"] == 1 and new["status"] == "已发布" and new["name"] == "新规则 W"
    r15 = (await client.get(f"{API}/rules/R-015")).json()
    assert r15["version"] == old15["version"] + 1 and r15["status"] == "已发布"
    assert r15["autoStage"]["enabled"] is flip
    r25 = (await client.get(f"{API}/rules/R-025")).json()
    assert r25["version"] == old25["version"] + 1 and r25["status"] == "已停用"
    assert not (await dyn.scalars(select(RuleDraft).where(RuleDraft.cr_code == cr))).all()
    vers = (await dyn.scalars(select(RuleVersion.by).where(RuleVersion.version == r15["version"]))).all()
    assert "王敏" in vers
    # 幂等
    assert await apply_cr(dyn, cr, by="U010001") == {"rules": [], "lifecycles": []}
    assert (await client.get(f"{API}/rules/R-015")).json()["version"] == r15["version"]
