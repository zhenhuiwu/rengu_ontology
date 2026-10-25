"""情景推演 / 生命周期 / 快照 section（rules、lifecycles）的构建与整版回滚恢复。"""

import json

from sqlalchemy import select

from app.core.config import get_settings
from app.modules.dynamic.models import ActionRun
from app.modules.dynamic.sections import BUILDERS
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.sections import registered_sections, restore_sections
from tests.dynamic_support import API, as_, dyn, err, fakes  # noqa: F401

RUN = {
    "scope": {"area": "华东一区", "category": "乳品", "weeks": 8},
    "drivers": {
        "pricePct": -5,
        "replenishPct": 10,
        "safetyDays": 7,
        "promo": "无",
        "promoDepth": 0,
        "promoWeeks": 0,
    },
}


def _json(rel: str):
    return json.loads((get_settings().seeds_json_dir / rel).read_text(encoding="utf-8"))


def _derived(name: str, sid: str):
    for x in _json(f"dynamic/derived/{name}.json"):
        a = x["args"][0]
        if (a.get("id") if isinstance(a, dict) else a) == sid:
            return x["result"]
    raise KeyError(sid)


async def test_scenario_list_detail_result(dyn, client):  # noqa: F811
    r = (await client.get(f"{API}/scenarios")).json()
    assert r["total"] == 18 and r["size"] == 12
    assert [x["id"] for x in r["items"]] == [x["id"] for x in _json("dynamic/SCENARIOS.json")][:12]
    f = (await client.get(f"{API}/scenarios", params={"status": "草稿"})).json()
    assert {x["id"] for x in f["items"]} == {"SCN-0005", "SCN-0001"}
    res = (await client.get(f"{API}/scenarios/SCN-0018/result")).json()
    want = next(x for x in _json("dynamic/SCENARIOS.json") if x["id"] == "SCN-0018")["result"]
    assert res == {k: v for k, v in want.items() if k != "impacts"}  # impacts 走分页接口
    imp = (await client.get(f"{API}/scenarios/SCN-0018/impacts")).json()
    assert imp["size"] == 6 and imp["total"] == len(want["impacts"]) and imp["items"] == want["impacts"][:6]
    plan = (await client.get(f"{API}/scenarios/SCN-0018/stage-plan")).json()
    assert plan == _derived("stagePlanOf", "SCN-0018")
    failed = (await client.get(f"{API}/scenarios/SCN-0013")).json()
    assert failed["status"] == "失败" and failed["failReason"]
    meta = (await client.get(f"{API}/scenarios/meta")).json()
    assert meta["areas"] == _json("dynamic/SC_AREAS.json")
    err(await client.get(f"{API}/scenarios/SCN-NOPE"), 404, "NOT_FOUND")


async def test_compare(dyn, client):  # noqa: F811
    c = (await client.get(f"{API}/scenarios:compare", params={"ids": "SCN-0018,SCN-0017"})).json()
    assert [x["id"] for x in c["items"]] == ["SCN-0018", "SCN-0017"]
    four = "SCN-0018,SCN-0017,SCN-0016,SCN-0015"
    err(await client.get(f"{API}/scenarios:compare", params={"ids": four}), 422, "COMPARE_LIMIT")
    err(
        await client.get(f"{API}/scenarios:compare", params={"ids": "SCN-0018,SCN-0005"}),
        422,
        "COMPARE_LIMIT",
    )


async def test_create_run_rename_stage(dyn, client, fakes):  # noqa: F811
    blank = await client.post(f"{API}/scenarios", json={})
    assert blank.status_code == 201 and blank.json()["name"] == "新方案"
    assert blank.json()["scope"] == {"area": "华东大区全部", "category": "全部品类", "weeks": 8}
    r = await client.post(f"{API}/scenarios", json={"from": "SCN-0018"})
    d = r.json()
    assert r.status_code == 201 and d["name"] == "双 11 乳品折扣 15% 两周（副本）" and d["status"] == "草稿"
    assert d["owner"] == "王敏" and d["baseVer"] == "v2026.10.3"
    sid = d["id"]
    nod = {**RUN, "drivers": {**RUN["drivers"], "pricePct": 0, "replenishPct": 0}}
    err(await client.post(f"{API}/scenarios/{sid}:run", json=nod), 422, "SCENARIO_NO_DRIVER")
    bad = {**RUN, "scope": {**RUN["scope"], "area": "火星"}}
    err(await client.post(f"{API}/scenarios/{sid}:run", json=bad), 422, "VALIDATION_FAILED")
    ran = (await client.post(f"{API}/scenarios/{sid}:run", json=RUN)).json()
    assert ran["status"] == "已完成" and ran["scope"] == RUN["scope"]
    res = (await client.get(f"{API}/scenarios/{sid}/result")).json()
    again = (await client.post(f"{API}/scenarios/{sid}:run", json=RUN)).json()
    assert (await client.get(f"{API}/scenarios/{sid}/result")).json()["kpis"] == res["kpis"]  # 同配置结果确定
    assert again["status"] == "已完成"
    assert (await client.patch(f"{API}/scenarios/{sid}", json={"name": "新名"})).json()["name"] == "新名"
    err(await client.patch(f"{API}/scenarios/{sid}", json={"name": "  "}), 422, "VALIDATION_FAILED")

    keys = [p["key"] for p in (await client.get(f"{API}/scenarios/{sid}/stage-plan")).json()][:2]
    assert keys
    err(await client.post(f"{API}/scenarios/{sid}:stage", json={"keys": ["nope"]}), 422, "VALIDATION_FAILED")
    st = (await client.post(f"{API}/scenarios/{sid}:stage", json={"keys": keys})).json()
    assert len(st["staged"]) == 2
    runs = (await dyn.scalars(select(ActionRun).where(ActionRun.code.in_(st["staged"])))).all()
    assert len(runs) == 2 and all(x.status.value == "待确认" for x in runs)
    assert fakes.submitter.calls == []
    locked = (await client.get(f"{API}/scenarios/{sid}")).json()
    assert locked["staged"] == st["staged"]
    err(await client.post(f"{API}/scenarios/{sid}:stage", json={"keys": keys}), 409, "SCENARIO_STAGED_LOCKED")
    err(await client.post(f"{API}/scenarios/{sid}:run", json=RUN), 409, "SCENARIO_STAGED_LOCKED")


async def test_not_owner(dyn, client):  # noqa: F811
    other = await as_(dyn, "陈立军")
    err(
        await client.post(f"{API}/scenarios/SCN-0018:run", json=RUN, headers=other), 403, "SCENARIO_NOT_OWNER"
    )
    err(
        await client.patch(f"{API}/scenarios/SCN-0018", json={"name": "x"}, headers=other),
        403,
        "SCENARIO_NOT_OWNER",
    )
    err(
        await client.post(f"{API}/scenarios/SCN-0018:stage", json={"keys": ["promo"]}, headers=other),
        403,
        "SCENARIO_NOT_OWNER",
    )


async def test_lifecycle_detail_changes_illegal(dyn, client):  # noqa: F811
    lc = (await client.get(f"{API}/object-types/order/lifecycle")).json()
    want = next(x for x in _json("dynamic/LIFECYCLES.json") if x["objectId"] == "order")
    assert lc["version"] == want["version"] and len(lc["states"]) == len(want["states"])
    assert [t["id"] for t in lc["transitions"]] == [t["id"] for t in want["transitions"]]
    assert [v["ver"] for v in lc["versions"]] == [v["ver"] for v in want["versions"]]
    ill = (await client.get(f"{API}/object-types/order/lifecycle/illegal", params={"since": "7d"})).json()
    assert ill == _derived("illegalOf", "order")
    ch = (await client.get(f"{API}/object-types/order/lifecycle/changes", params={"illegal": 1})).json()
    assert ch["size"] == 6 and ch["total"] == sum(x["count"] for x in ill)
    assert all(x["trId"] is None for x in ch["items"])
    all_ = (await client.get(f"{API}/object-types/order/lifecycle/changes", params={"size": 200})).json()
    assert [x["id"] for x in all_["items"]] == [x["id"] for x in _derived("changesOf", "order")][:200]
    # 历史版本：迁移变化 / 非法汇总为空，状态机为该版发布时生效的
    old = (await client.get(f"{API}/object-types/order/lifecycle", params={"ver": "v2026.09.3"})).json()
    assert old["version"] < lc["version"]
    empty = await client.get(f"{API}/object-types/order/lifecycle/changes", params={"ver": "v2026.09.3"})
    assert empty.json()["total"] == 0
    err(
        await client.get(f"{API}/object-types/order/lifecycle", params={"ver": "v1999.01.1"}),
        404,
        "NOT_FOUND",
    )
    err(await client.get(f"{API}/object-types/nope/lifecycle"), 404, "NOT_FOUND")


async def test_lifecycle_draft(dyn, client):  # noqa: F811
    url = f"{API}/object-types/order/lifecycle/draft"
    empty = {"addStates": [], "addTransitions": [], "editTransitions": {}, "removeTransitions": []}
    err(await client.put(url, json=empty), 409, "STATE_INVALID")  # 没有变更单：已发布只读
    edits = {
        **empty,
        "cr": "CR-9001",
        "addStates": [{"key": "已退货", "name": "已退货", "kind": "终止", "desc": "x"}],
    }
    r = await client.put(url, json=edits)
    assert r.status_code == 200, r.text
    d = (
        await client.get(f"{API}/object-types/order/lifecycle", params={"view": "draft", "cr": "CR-9001"})
    ).json()
    assert d["draft"]["cr"] == "CR-9001"  # 草稿改动由页面叠加（LcDraft 形状，同 mock）
    assert [s["key"] for s in d["draft"]["edits"]["addStates"]] == ["已退货"]
    cur = (await client.get(f"{API}/object-types/order/lifecycle")).json()
    assert "已退货" not in [s["key"] for s in cur["states"]]
    dup = {
        **empty,
        "cr": "CR-9001",
        "addStates": [{"key": "待支付", "name": "待支付", "kind": "中间", "desc": ""}],
    }
    err(await client.put(url, json=dup), 422, "VALIDATION_FAILED")


async def test_sections_registered_seeded_and_restorable(dyn, client):  # noqa: F811
    reg = registered_sections()
    assert {"rules", "lifecycles"} <= set(reg) and reg["rules"] is BUILDERS[0]
    rels = {r.ver: r for r in (await dyn.scalars(select(OntologyRelease))).all()}
    latest = max(rels.values(), key=lambda r: r.published_at)
    # 最新一版快照 == 现在的定义表
    for b in BUILDERS:
        built = await b.build_section(dyn, None, [])
        assert built == latest.snapshot[b.section], b.section
    # 整版回滚到 v2026.09.3：生命周期回到当时的版本，规则回到当时的定义
    old = rels["v2026.09.3"]
    await restore_sections(dyn, old.snapshot, latest.id)
    await dyn.flush()
    for b in BUILDERS:
        assert await b.build_section(dyn, None, []) == old.snapshot[b.section], b.section
    lc = (await client.get(f"{API}/object-types/order/lifecycle")).json()
    assert lc["version"] == old.snapshot["lifecycles"]["order"]["version"]
    # 再回到最新
    await restore_sections(dyn, latest.snapshot, latest.id)
    await dyn.flush()
    for b in BUILDERS:
        assert await b.build_section(dyn, None, []) == latest.snapshot[b.section], b.section
