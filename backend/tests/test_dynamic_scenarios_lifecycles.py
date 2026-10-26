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
    r = await client.post(f"{API}/scenarios/{sid}:run", json=RUN)
    # 异步：202 + 运行中；后台任务（测试传输里在响应后同步跑完）算完后为已完成
    assert r.status_code == 202 and r.json()["status"] == "运行中" and r.json()["scope"] == RUN["scope"]
    assert (await client.get(f"{API}/scenarios/{sid}")).json()["status"] == "已完成"
    res = (await client.get(f"{API}/scenarios/{sid}/result")).json()
    assert (await client.post(f"{API}/scenarios/{sid}:run", json=RUN)).status_code == 202
    assert (await client.get(f"{API}/scenarios/{sid}/result")).json()["kpis"] == res["kpis"]  # 同配置结果确定
    assert (await client.get(f"{API}/scenarios/{sid}")).json()["status"] == "已完成"
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
        "addStates": [{"key": "已退货", "name": "已退货", "kind": "终态", "desc": "x"}],
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


async def test_result_running_and_failed(dyn, client, session):  # noqa: F811
    from app.modules.dynamic.rules_models import Scenario, ScenarioStatus

    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    s = await session.scalar(select(Scenario).where(Scenario.code == sid))
    s.status = ScenarioStatus.running
    await session.flush()
    r = await client.get(f"{API}/scenarios/{sid}/result")
    assert r.status_code == 202 and r.json() == {"status": "运行中"}
    s.status, s.fail_reason = ScenarioStatus.failed, "推演失败：x"
    await session.flush()
    err(await client.get(f"{API}/scenarios/{sid}/result"), 409, "SCENARIO_FAILED")


async def test_compute_db_error_marks_failed(dyn, client, monkeypatch):  # noqa: F811
    from sqlalchemy import text

    from app.modules.dynamic.rules_service import ScenarioService

    async def boom(self, s):
        await self.session.execute(text("select 1/0"))  # 真实数据库错误

    monkeypatch.setattr(ScenarioService, "_compute", boom)
    sid = (await client.post(f"{API}/scenarios", json={})).json()["id"]
    assert (await client.post(f"{API}/scenarios/{sid}:run", json=RUN)).status_code == 202
    assert (await client.get(f"{API}/scenarios/{sid}")).json()["status"] == "失败"
    err(await client.get(f"{API}/scenarios/{sid}/result"), 409, "SCENARIO_FAILED")


async def test_lifecycle_draft_registers_cr_items(dyn, client):  # noqa: F811
    cr = (await client.post(f"{API}/change-requests", json={"title": "生命周期闭环"})).json()["id"]
    empty = {"addStates": [], "addTransitions": [], "editTransitions": {}, "removeTransitions": []}
    edits = {
        **empty,
        "cr": cr,
        "addStates": [{"key": "已退货", "name": "已退货", "kind": "终态", "desc": ""}],
    }
    r = await client.put(f"{API}/object-types/order/lifecycle/draft", json=edits)
    assert r.status_code == 200, r.text
    assert r.json()["crChanges"] >= 1
    rows = (
        await client.get(f"{API}/change-requests/{cr}/items", params={"kind": "object", "targetId": "order"})
    ).json()
    assert [(x["section"], x["key"], x["op"]) for x in rows] == [("lc_states", "state.已退货", "add")]
    # 静态侧放弃整个 object 目标后，残留的生命周期草稿不再显示
    assert (await client.delete(f"{API}/change-requests/{cr}/targets/object/order")).status_code == 204
    d = (await client.get(f"{API}/object-types/order/lifecycle", params={"view": "draft", "cr": cr})).json()
    assert not (d.get("draft") or {}).get("edits", {}).get("addStates")


async def test_apply_cr_promotes_lifecycle_draft(dyn, client):  # noqa: F811
    """发布写回 apply_cr：生命周期草稿 edits 生成下一版状态机；重复调用无副作用。"""
    from app.modules.dynamic.writeback import apply_cr

    cr = (await client.post(f"{API}/change-requests", json={"title": "生命周期发布"})).json()["id"]
    old = (await client.get(f"{API}/object-types/order/lifecycle")).json()
    t0 = old["transitions"][0]
    edits = {
        "cr": cr,
        "addStates": [{"key": "已退货", "name": "已退货", "kind": "终态", "desc": "", "x": 9, "y": 9}],
        "addTransitions": [
            {
                "id": "T99",
                "from": old["states"][0]["key"],
                "to": "已退货",
                "trigger": t0["trigger"],
                "by": "人工",
                "guard": "",
                "effect": "",
                "weekly": 0,
            }
        ],
        "editTransitions": {},
        "removeTransitions": [old["transitions"][-1]["id"]],
    }
    r = await client.put(f"{API}/object-types/order/lifecycle/draft", json=edits)
    assert r.status_code == 200, r.text
    out = await apply_cr(dyn, cr, by="U010001")
    assert out == {"rules": [], "lifecycles": ["order"]}
    lc = (await client.get(f"{API}/object-types/order/lifecycle")).json()
    assert lc["version"] == old["version"] + 1
    assert [s["key"] for s in lc["states"]] == [s["key"] for s in old["states"]] + ["已退货"]
    ids = [t["id"] for t in lc["transitions"]]
    assert "T99" in ids and old["transitions"][-1]["id"] not in ids and len(ids) == len(old["transitions"])
    assert await apply_cr(dyn, cr, by="U010001") == {"rules": [], "lifecycles": []}
    assert (await client.get(f"{API}/object-types/order/lifecycle")).json()["version"] == lc["version"]


def test_apply_edits_rejects_bad_kind():
    import pytest

    from app.core.errors import AppError
    from app.modules.dynamic.writeback import apply_edits

    with pytest.raises(AppError) as e:
        bad = {"addStates": [{"key": "x", "name": "x", "kind": "终止"}]}
        apply_edits({"states": [], "transitions": []}, bad)
    assert e.value.code == "WRITEBACK_FAILED"


async def test_lifecycle_draft_rejects_bad_shapes(dyn, client):  # noqa: F811
    url = f"{API}/object-types/order/lifecycle/draft"
    base = {
        "cr": "CR-9001",
        "addStates": [],
        "addTransitions": [],
        "editTransitions": {},
        "removeTransitions": [],
    }
    st = {"key": "已退货", "name": "已退货", "kind": "终态", "desc": ""}
    tr = {"id": "T-x", "from": "待支付", "to": "已退货", "trigger": "人工", "by": "客服"}
    cases = [
        ({"addStates": [st], "addTransitions": [{**tr, "from": ["待支付"]}]}, "addTransitions[0].from"),
        ({"addStates": [st], "addTransitions": [{**tr, "to": {"a": 1}}]}, "addTransitions[0].to"),
        ({"addStates": [{**st, "key": ["x"]}]}, "addStates[0].key"),
        ({"addStates": [{**st, "kind": "终止"}]}, "addStates[0].kind"),
    ]
    for extra, loc in cases:
        r = await client.put(url, json={**base, **extra})
        err(r, 422, "VALIDATION_FAILED")
        assert loc in r.text, (loc, r.text)


def test_apply_edits_missing_key_409():
    import pytest

    from app.core.errors import AppError
    from app.modules.dynamic.writeback import apply_edits

    for bad in (
        {"addStates": [{"name": "x", "kind": "终态"}]},
        {"addTransitions": [{"from": "a", "to": "a", "trigger": "人工"}]},
    ):
        with pytest.raises(AppError) as e:
            apply_edits({"states": [{"key": "a", "name": "a", "kind": "初始"}], "transitions": []}, bad)
        assert e.value.code == "WRITEBACK_FAILED" and e.value.status_code == 409
