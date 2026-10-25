"""qa 只读接口：形状 / 数值逐条对 mock（seeds/json/qa），排序、筛选、分页、预览。"""

import json

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.seeds.loader import run_seeds

API = "/api/qa/v1"


def mock(name: str):
    return json.loads((get_settings().seeds_json_dir / "qa" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
async def qa(users, client):
    await run_seeds(users, ["qa"])
    await users.flush()
    return client


async def get(c, url, user: str | None = None, **params):
    headers = {"X-Demo-As": user} if user else {}
    r = await c.get(API + url, params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- 工作台


async def test_sessions_paging_and_range(qa):
    body = await get(qa, "/sessions")
    assert body["size"] == 12 and body["page"] == 1 and len(body["items"]) == 12
    assert body["items"][0] == {"id": "S0001", "title": "华东大区复购客户销售额", "date": "2026-10-25"}
    dates = [s["date"] for s in (await get(qa, "/sessions", size=200))["items"]]
    assert dates == sorted(dates, reverse=True)
    today = (await get(qa, "/sessions", range="today", size=200))["items"]
    assert today and all(s["date"] == "2026-10-25" for s in today)
    week = (await get(qa, "/sessions", range="7d", size=200))["items"]
    assert all(s["date"] >= "2026-10-19" for s in week) and len(week) >= len(today)
    hit = (await get(qa, "/sessions", q="复购", size=200))["items"]
    assert hit and all("复购" in s["title"] for s in hit)


async def test_session_detail_turns(qa):
    s = await get(qa, "/sessions/S0001")
    assert s["ownerId"] == get_settings().dev_user and s["caliber"] == "不含税"
    demo = mock("QA_DEMO_TURNS")
    assert [t["id"] for t in s["turns"]] == [t["id"] for t in demo]
    for got, want in zip(s["turns"], demo, strict=True):
        assert got == want


async def test_session_other_owner_hidden(qa, session):
    other = (
        await session.execute(
            text("SELECT code FROM sys_user WHERE code <> :c LIMIT 1"), {"c": get_settings().dev_user}
        )
    ).scalar_one()
    r = await qa.get(API + "/sessions/S0001", headers={"X-Demo-As": other})
    assert r.status_code == 404


async def test_favorites_and_options(qa):
    fav = await get(qa, "/favorites")
    assert fav["size"] == 10 and fav["items"][0]["uses"] >= fav["items"][-1]["uses"]
    opt = await get(qa, "/session-options")
    assert opt["scopes"] == mock("QA_SCOPE_OPTIONS") and opt["calibers"] == mock("QA_CALIBER_OPTIONS")
    assert opt["metric"] == mock("QA_METRIC")


# ---------------------------------------------------------------- 页面与模板


async def _owner_codes(session) -> dict[str, str]:
    rows = await session.execute(
        text("SELECT p.code, u.code FROM qa_page p JOIN sys_user u ON u.id = p.owner_id")
    )
    return dict(rows.all())


async def test_page_cards_match_mock(qa, session):
    owners = await _owner_codes(session)
    for p in mock("MY_PAGES"):
        got = await get(qa, f"/pages/{p['id']}", user=owners[p["id"]])
        assert got["cards"] == p["cards"], p["id"]
        assert got["name"] == p["name"] and got["sourceKind"] == p["sourceKind"]
        tags = {}
        for c in p["cards"]:
            tags[f"{c['metric']} {c['version']}"] = c.get("pending")
        assert got["calibers"] == [{"label": k, **({"pending": v} if v else {})} for k, v in tags.items()]


async def test_pages_list_filters(qa):
    full = await get(qa, "/pages", size=200)
    assert full["size"] == 200 and full["fromWorkbenchCount"] == sum(
        1 for p in full["items"] if p["sourceKind"] == "问数工作台"
    )
    brief = await get(qa, "/pages", view="brief")
    assert brief["size"] == 15 and set(brief["items"][0]) == {"id", "name", "cards", "updatedAt", "shared"}
    shared = (await get(qa, "/pages", share="已共享", view="brief", size=200))["items"]
    assert shared and all(p["shared"] for p in shared)
    st = (await get(qa, "/pages", status="口径待更新", size=200))["items"]
    assert all(p["status"] == "口径待更新" for p in st)


async def test_pages_preview_includes_shared(qa, session):
    owners = await _owner_codes(session)
    member = (
        await session.execute(
            text(
                "SELECT u.code, p.code FROM qa_page_share_member m JOIN sys_user u ON u.id = m.user_id "
                "JOIN qa_page p ON p.id = m.page_id WHERE p.share_mode = 'members' LIMIT 1"
            )
        )
    ).first()
    viewer, page_code = member
    ids = [p["id"] for p in (await get(qa, "/pages", previewAs=viewer, size=200))["items"]]
    assert page_code in ids
    own = [p["id"] for p in (await get(qa, "/pages", user=viewer, size=200))["items"]]
    assert all(owners[i] == viewer for i in own)
    # 被共享人也能打开详情
    assert (await get(qa, f"/pages/{page_code}", user=viewer))["id"] == page_code


async def test_templates(qa):
    t = await get(qa, "/templates", size=200)
    assert t["total"] == len(mock("PAGE_TEMPLATES"))
    one = await get(qa, "/templates/T001")
    want = next(x for x in mock("PAGE_TEMPLATES") if x["id"] == "T001")
    assert one["cards"] == want["cards"] and one["desc"] == want["desc"]
    scene = (await get(qa, "/templates", scene=want["scene"], size=200))["items"]
    assert all(x["scene"] == want["scene"] for x in scene)


async def test_object_types(qa):
    kinds = [o["kind"] for o in await get(qa, "/objects/types")]
    assert kinds and kinds[0] == "store"


# ---------------------------------------------------------------- 调优


async def test_tuning_summary(qa):
    s = await get(qa, "/tuning/summary")
    certs, fbs, fq = mock("CERT_QAS"), mock("FEEDBACKS"), mock("FREQ_QS")
    assert s == {
        "version": mock("CUR_VERSION"),
        "dataAsOf": mock("TUNING_CUTOFF"),
        "recertCount": sum(c["status"] == "待重新认证" for c in certs),
        "openFeedback": sum(f["status"] == "待处理" for f in fbs),
        "uncertFreq": sum(not f.get("certId") for f in fq),
        "evalTotal": len(mock("EVAL_QS")),
    }
    assert (await get(qa, "/tuning/summary", ver="v2026.10.1"))["version"] == "v2026.10.1"


async def test_certs_order_and_filter(qa):
    body = await get(qa, "/tuning/certs", size=200)
    rank = {"待重新认证": 0, "待认证": 1, "已认证": 2, "已停用": 3}
    ranks = [rank[c["status"]] for c in body["items"]]
    assert ranks == sorted(ranks) and body["total"] == len(mock("CERT_QAS"))
    by_id = {c["id"]: c for c in mock("CERT_QAS")}
    for c in body["items"]:
        assert c["variants"] == by_id[c["id"]]["variants"] and c["plan"] == by_id[c["id"]]["plan"]
    v = by_id["CQ-001"]["variants"][0]
    assert "CQ-001" in [c["id"] for c in (await get(qa, "/tuning/certs", kw=v))["items"]]
    m = (await get(qa, "/tuning/certs", metric="refund", size=200))["items"]
    assert m and all(c["plan"]["metric"] == "refund" for c in m)
    assert (await get(qa, "/tuning/certs/CQ-001"))["planText"] == "退款率 v2 · 按 大区 · 上个月"


async def test_eval_runs_match_mock(qa):
    runs = mock("EVAL_RUNS")
    body = await get(qa, "/tuning/eval/runs", size=200)
    assert body["total"] == len(runs) and body["items"][0]["status"] == "待运行"
    by_id = {r["id"]: r for r in runs}
    for x in body["items"]:
        want = by_id[x["id"]]
        assert x["failCount"] == len(want["fails"])
        assert x["passRate"] == pytest.approx(want["passed"] / want["total"] if want["total"] else 0)
        d = await get(qa, f"/tuning/eval/runs/{x['id']}", size=200)
        assert d["fails"]["items"] == want["fails"], x["id"]
    d = await get(qa, "/tuning/eval/runs/ER-0023")
    assert d["fails"]["size"] == 12
    q = await get(qa, "/tuning/eval/runs/ER-0025")
    assert q["replayHints"].keys() == {"CR-0008", "CR-0015"}
    assert {e["cr"] for e in q["replayQuestions"]} <= {"CR-0008", "CR-0015"} and q["replayQuestions"]
    trig = (await get(qa, "/tuning/eval/runs", trigger="回放", size=200))["items"]
    assert trig and all("回放" in x["trigger"] for x in trig)


async def test_eval_questions(qa):
    body = await get(qa, "/tuning/eval/questions", size=200)
    want = {q["id"]: q for q in mock("EVAL_QS")}
    assert body["total"] == len(want)
    for q in body["items"]:
        assert q["expect"] == want[q["id"]]["expect"] and q["last"] == want[q["id"]]["last"]
    assert sum(g["n"] for g in body["groupCounts"]) == len(want)
    fail = (await get(qa, "/tuning/eval/questions", last="失败", size=200))["items"]
    assert all(q["last"] == "失败" for q in fail)


async def test_feedbacks(qa):
    body = await get(qa, "/tuning/feedbacks", size=200)
    assert body["total"] == len(mock("FEEDBACKS"))
    st = [x["status"] == "待处理" for x in body["items"]]
    assert st == sorted(st, reverse=True)
    syn = (await get(qa, "/tuning/feedbacks", fixKind="同义词", size=200))["items"]
    assert syn and all(x["fix"]["kind"] == "同义词" for x in syn)
    d = await get(qa, f"/tuning/feedbacks/{body['items'][0]['id']}")
    assert "certBlock" in d and "relatedEvalCount" in d


async def test_freq_rank(qa):
    body = await get(qa, "/tuning/freq", size=200)
    assert [x["rank"] for x in body["items"]] == list(range(1, len(body["items"]) + 1))
    hits = [x["hits30"] for x in body["items"]]
    assert hits == sorted(hits, reverse=True)
    un = (await get(qa, "/tuning/freq", cert="未认证", size=200))["items"]
    assert un and all("certId" not in x for x in un)
    p7 = (await get(qa, "/tuning/freq", period=7, size=200))["items"]
    assert [x["hits7"] for x in p7] == sorted((x["hits7"] for x in p7), reverse=True)


async def test_synonyms_and_plan_options(qa):
    assert await get(qa, "/tuning/synonyms") == mock("TUNED_SYNONYMS")
    po = await get(qa, "/tuning/plan-options")
    assert po["dims"] == mock("TUNING_DIMS") and len(po["metrics"]) == len(mock("T_METRICS"))
