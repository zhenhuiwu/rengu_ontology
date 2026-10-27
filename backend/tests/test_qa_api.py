"""qa 只读接口：形状 / 数值逐条对 mock（seeds/json/qa），排序、筛选、分页、预览。"""

import json

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from tests import qa_page_test_support
from tests.function_access_support import grant_fixture_roles
from tests.qa_access_support import seed_qa_access

qa_workspace = qa_page_test_support.qa_workspace
users = qa_page_test_support.users

API = "/api/qa/v1"


def mock(name: str):
    return json.loads((get_settings().seeds_json_dir / "qa" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    # 拥有者/共享成员仍需真实页面功能权；R-05 保留华南范围，不能读取他人保存的全量卡片。
    codes = await users.scalars(
        text(
            "SELECT u.code FROM sys_user u WHERE u.id IN ("
            "SELECT owner_id FROM qa_page UNION SELECT user_id FROM qa_page_share_member)"
        )
    )
    await grant_fixture_roles(users, {code: "R-05" for code in codes})
    await grant_fixture_roles(users, {"U010006": "R-05"})
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
    assert body["size"] == 10 and body["page"] == 1 and len(body["items"]) == 10
    all_sessions = (await get(qa, "/sessions", size=200))["items"]
    assert {"id": "S0001", "title": "华东大区复购客户销售额", "date": "2026-10-25"} in all_sessions
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
        assert got == {**want, "pinnable": False}


async def test_session_other_owner_hidden(qa, session):
    other = (
        await session.execute(
            text("SELECT code FROM sys_user WHERE code <> :c LIMIT 1"), {"c": get_settings().dev_user}
        )
    ).scalar_one()
    # 先满足功能权限，再独立验证会话拥有者边界。
    await grant_fixture_roles(session, {other: "R-01"})
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
        assert len(got["cards"]) == len(p["cards"]), p["id"]
        for card, original in zip(got["cards"], p["cards"], strict=True):
            assert card["denied"] and card["unavailable"]
            assert all(card[key] == original[key] for key in ("key", "title", "viz", "metric", "version"))
            assert not set(card) & {"rows", "items", "value", "series", "labels"}
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
    assert brief["size"] == 10 and set(brief["items"][0]) == {"id", "name", "cards", "updatedAt", "shared"}
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
    assert page_code in own and any(owners[i] != viewer for i in own)
    # 被共享人也能打开详情
    assert (await get(qa, f"/pages/{page_code}", user=viewer))["id"] == page_code


async def test_templates(qa):
    t = await get(qa, "/templates", size=200)
    assert t["total"] >= len(mock("PAGE_TEMPLATES"))
    assert {row["id"] for row in mock("PAGE_TEMPLATES")} <= {row["id"] for row in t["items"]}
    one = await get(qa, "/templates/T001")
    want = next(x for x in mock("PAGE_TEMPLATES") if x["id"] == "T001")
    assert one["desc"] == want["desc"] and not one["canCreate"]
    assert len(one["cards"]) == len(want["cards"])
    assert all(not set(card) & {"rows", "items", "value", "series", "labels"} for card in one["cards"])
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
        "version": "v2026.10.3",  # 静态定义与发布记录均来自实际种子表。
        "dataAsOf": "",  # 无统一业务水位，不再复用演示时间。
        "recertCount": sum(c["status"] == "待重新认证" for c in certs),
        "openFeedback": sum(f["status"] == "待处理" for f in fbs),
        "uncertFreq": sum(not f.get("certId") for f in fq),
        "evalTotal": len(mock("EVAL_QS")),
    }
    response = await qa.get(API + "/tuning/summary", params={"ver": "missing"})
    assert response.status_code == 404


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
    assert q["replayHints"] == {}  # 未存储回放提示，不能拿固定演示 CR 文案补齐。
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
    assert "大区" in po["dims"] and len(po["metrics"]) == 15
    assert next(m for m in po["metrics"] if m["key"] == "orders")["code"] == "order_cnt"


# ---------------------------------------------------------------- 卡片判权（P1/P3，真实角色与行范围）

_DATA_KEYS = {"value", "unit", "sub", "series", "labels", "rows"}


def _assert_denied(cards, want):
    assert [c["key"] for c in cards] == [c["key"] for c in want]
    for got, w in zip(cards, want, strict=True):
        assert got["denied"] is True
        assert not (_DATA_KEYS & set(got)), got
        frame = {k: w[k] for k in ("key", "title", "viz", "metric", "version", "pending") if k in w}
        assert {k: v for k, v in got.items() if k not in ("denied", "unavailable")} == frame


async def test_shared_member_gets_denied_cards(qa):
    want = next(p for p in mock("OTHER_PAGES") if p["id"] == "P905")["cards"]
    got = await get(qa, "/pages/P905", user="U010231")  # 苏刚：P905（周婷）的共享成员，非本体管理员
    _assert_denied(got["cards"], want)
    # 口径标签仍可见（只是元数据）
    assert {c["label"] for c in got["calibers"]} == {f"{c['metric']} {c['version']}" for c in want}


async def test_owner_cannot_reuse_unprovenanced_seed_values(qa, session):
    owners = await _owner_codes(session)
    want = next(p for p in mock("OTHER_PAGES") if p["id"] == "P905")["cards"]
    got = await get(qa, "/pages/P905", user=owners["P905"])
    _assert_denied(got["cards"], want)
    # Owning or previewing the owner does not prove old sample provenance.
    got = await get(qa, "/pages/P905", previewAs=owners["P905"])
    _assert_denied(got["cards"], want)


async def test_preview_follows_row_filter(qa):
    want = next(p for p in mock("OTHER_PAGES") if p["id"] == "P905")["cards"]
    # 管理员预览苏刚：真实 R-05 范围限制 → 共享页卡片无权限（详情和列表一致）。
    _assert_denied((await get(qa, "/pages/P905", previewAs="U010231"))["cards"], want)
    items = (await get(qa, "/pages", previewAs="U010231", size=200))["items"]
    p905 = next(p for p in items if p["id"] == "P905")
    _assert_denied(p905["cards"], want)
    own = [p for p in items if p["id"] in ("P901", "P902", "P903")]
    assert own and all(all(c.get("denied") for c in p["cards"]) for p in own)


async def test_admin_shared_member_still_requires_execution_provenance(qa, session):
    """An unrestricted row grant does not make sample caches real execution results."""
    from app.core.config import get_settings as gs

    admin = gs().demo_admin_codes[0]
    row = (
        await session.execute(
            text(
                "SELECT p.code FROM qa_page p JOIN qa_page_share_member m ON m.page_id = p.id "
                "JOIN sys_user u ON u.id = m.user_id JOIN sys_user o ON o.id = p.owner_id "
                "WHERE u.code = :c AND o.code <> :c LIMIT 1"
            ),
            {"c": admin},
        )
    ).first()
    if row is None:
        owners = await _owner_codes(session)
        assert owners["P905"] != admin
        headers = {"X-Demo-As": owners["P905"]}
        page = (await qa.get(API + "/pages/P905", headers=headers)).json()
        response = await qa.put(
            API + "/pages/P905/share",
            headers=headers,
            json={
                "mode": "指定成员",
                "members": [{"userId": admin, "perm": "可查看"}],
                "link": False,
                "allowExport": False,
                "lockVersion": page["lockVersion"],
            },
        )
        assert response.status_code == 200, response.text
        row = ("P905",)
    got = await get(qa, f"/pages/{row[0]}", user=admin)
    assert all(c.get("denied") for c in got["cards"])


async def test_keywords_trimmed(qa):
    a = await get(qa, "/sessions", q="复购", size=200)
    b = await get(qa, "/sessions", q="  复购 ", size=200)
    assert a == b and a["total"] > 0
    assert await get(qa, "/sessions", q="   ", size=200) == await get(qa, "/sessions", size=200)
    assert await get(qa, "/pages", kw=" 周报 ", view="brief", size=200) == await get(
        qa, "/pages", kw="周报", view="brief", size=200
    )
    assert await get(qa, "/tuning/certs", kw="  ", size=200) == await get(qa, "/tuning/certs", size=200)
    fav = await get(qa, "/favorites", size=200)
    word = fav["items"][0]["question"][:2]
    assert await get(qa, "/favorites", q=f" {word}  ", size=200) == await get(
        qa, "/favorites", q=word, size=200
    )


# ------------------------------------------------ 分页收拢（CONVENTIONS §3，前端 paginate.ts:13）

NO_HIT = "__没有这个关键词__"


def _items_page(all_items: list, page: int, size: int) -> tuple[list, int]:
    """前端 paginate()：page = min(max(1, page), max(1, ceil(total/size)))。"""
    pages = max(1, -(-len(all_items) // size))
    p = min(max(1, page), pages)
    return all_items[(p - 1) * size : p * size], p


async def _assert_clamped(c, url: str, *, pick=lambda b: b, kw: str | None = None, size: int = 3, **params):
    """越界 → 最后一页（page 也收拢）；page=0 / -3 → 第 1 页；total=0 → 第 1 页、空列表。"""
    full = pick(await get(c, url, size=200, **params))
    assert full["total"] > size, f"{url} 数据不够翻页"
    for req in (999, full["total"] // size + 2, 0, -3, 1, 2):
        body = pick(await get(c, url, page=req, size=size, **params))
        want, page = _items_page(full["items"], req, size)
        assert (body["page"], body["size"], body["total"]) == (page, size, full["total"]), (url, req)
        assert body["items"] == want, (url, req)
    if kw:
        for req in (1, 0, -3, 5):
            empty = pick(await get(c, url, page=req, size=size, **{kw: NO_HIT}, **params))
            assert (empty["page"], empty["total"], empty["items"]) == (1, 0, []), (url, req)


async def test_paging_clamped_sessions(qa):
    await _assert_clamped(qa, "/sessions", kw="q")
    assert (await get(qa, "/sessions", page=0))["size"] == 10


async def test_paging_clamped_lists(qa):
    await _assert_clamped(qa, "/favorites", kw="q")
    await _assert_clamped(qa, "/pages", kw="kw", view="brief")
    await _assert_clamped(qa, "/templates", kw="kw")
    await _assert_clamped(qa, "/tuning/certs", kw="kw")
    await _assert_clamped(qa, "/tuning/eval/runs")
    await _assert_clamped(qa, "/tuning/eval/questions", kw="kw")
    await _assert_clamped(qa, "/tuning/feedbacks", kw="kw")


async def test_paging_clamped_freq(qa):
    """T18 自己拼 SQL（count + select 两条），先 clamp(total) 再算 offset。"""
    await _assert_clamped(qa, "/tuning/freq", kw="kw")
    await _assert_clamped(qa, "/tuning/freq", kw="kw", period=7)
    body = await get(qa, "/tuning/freq", page=999)
    assert body["size"] == 15 and body["items"] and body["page"] == max(1, -(-body["total"] // 15))
    assert body["items"][-1]["rank"] == body["total"]


async def test_paging_clamped_eval_run_fails(qa):
    """T10 详情里的 fails 子分页（service.run_detail）。"""
    runs = (await get(qa, "/tuning/eval/runs", size=200))["items"]
    rid = max(runs, key=lambda x: x["failCount"])["id"]
    await _assert_clamped(qa, f"/tuning/eval/runs/{rid}", pick=lambda b: b["fails"])
    assert (await get(qa, f"/tuning/eval/runs/{rid}", page=-3))["fails"]["size"] == 12
    zero = next(x["id"] for x in runs if x["failCount"] == 0)
    for req in (1, 0, 7):
        f = (await get(qa, f"/tuning/eval/runs/{zero}", page=req))["fails"]
        assert (f["page"], f["total"], f["items"]) == (1, 0, [])


@pytest.mark.parametrize("url", ["/sessions", "/tuning/freq", "/tuning/eval/runs/ER-0023"])
@pytest.mark.parametrize("size", [0, 201])
async def test_paging_size_still_validated(qa, url, size):
    """size 照骨架校验（1–200，越界 422）；一个请求一个用例（422 会回滚测试共用的会话）。"""
    r = await qa.get(API + url, params={"size": size})
    assert r.status_code == 422, r.text
