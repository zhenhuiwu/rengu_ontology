"""审计日志（admin，前端 src/pages/admin/AuditLog.vue，api-index §4.3）。

对照方法：用 export-mock 导出的 AUDIT_LOGS.json 在 Python 里按 AuditLog.vue 的 rows 过滤逻辑算出期望，
再用 nextCursor 翻完所有页，逐条比对（不重、不漏、顺序一致）。
"""

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import get_settings
from app.modules.admin.models import AuditLog, AuditResult
from app.modules.admin.repo import AuditCursor, insert_audit
from tests.function_access_support import grant_fixture_roles

MOCK = json.loads((get_settings().seeds_json_dir / "system" / "AUDIT_LOGS.json").read_text("utf-8"))

URL = "/api/admin/v1/audit-logs"
FMT = "%Y-%m-%d %H:%M:%S"
NOW = datetime(2026, 10, 25, 16, 12, 41)  # noqa: DTZ001 AuditLog.vue 的 mock「现在」
H24 = ((NOW - timedelta(hours=24)).strftime(FMT), NOW.strftime(FMT))
D30 = ((NOW - timedelta(days=30)).strftime(FMT), NOW.strftime(FMT))


def expect(frm, to, action="全部", result="全部", field="actor", kw=""):
    """AuditLog.vue 的 rows computed，一字不差。"""
    k = kw.strip()
    return [
        r
        for r in MOCK
        if frm <= r["time"] <= to
        and (action == "全部" or r["action"] == action)
        and (result == "全部" or r["result"] == result)
        and (not k or k in r[field])
    ]


@pytest.fixture
async def authorized(users):
    await grant_fixture_roles(users)


async def page_all(client, size=20, **params):
    out, cursor, pages = [], None, 0
    while True:
        q = {**params, "size": size}
        if cursor:
            q["cursor"] = cursor
        r = await client.get(URL, params=q)
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body) == {"items", "nextCursor"}  # CursorResult：没有 total
        out += body["items"]
        pages += 1
        cursor = body["nextCursor"]
        if cursor is None:
            return out, pages
        assert len(body["items"]) == size  # 不是最后一页就一定是满页
        assert pages < 1000


def test_mock_export_shape():
    assert len(MOCK) == 521 and len({r["id"] for r in MOCK}) == 521
    assert sum(r["id"].startswith("ALCR") for r in MOCK) == 9
    assert [r["time"] for r in MOCK] == sorted((r["time"] for r in MOCK), reverse=True)


async def test_seed_count_and_idempotent(users):
    from app.seeds.loader import run_seeds

    await run_seeds(users, ["admin"])
    assert await users.scalar(select(func.count()).select_from(AuditLog)) == 521
    row = await users.scalar(select(AuditLog).where(AuditLog.code == "ALCR3"))
    assert row.action == "发布本体" and row.is_seed and row.result is AuditResult.success


async def test_default_24h_first_page_matches_mock(client, users, authorized):
    r = await client.get(URL, params={"from": H24[0], "to": H24[1]})
    assert r.status_code == 200
    body = r.json()
    exp = expect(*H24)
    assert body["items"] == exp[:20]  # 字段、取值、顺序都和 mock 一样
    assert body["nextCursor"] and len(exp) > 20


async def test_page_through_all_no_dup_no_gap(client, users, authorized):
    got, pages = await page_all(client, size=37, **{"from": D30[0], "to": D30[1]})
    assert got == MOCK and pages == -(-521 // 37)
    assert len({r["id"] for r in got}) == 521


async def test_exact_multiple_has_no_trailing_empty_page(client, users, authorized):
    """前端 paginateCursor：end < rows.length 才给 nextCursor → 刚好满页时最后一页 nextCursor=null。"""
    n = len(expect(*H24, action="导出"))
    assert 0 < n <= 200
    r = await client.get(URL, params={"from": H24[0], "to": H24[1], "action": "导出", "size": n})
    assert len(r.json()["items"]) == n and r.json()["nextCursor"] is None


@pytest.mark.parametrize(
    "flt",
    [
        {"action": "导出"},
        {"action": "全部", "result": "失败"},
        {"result": "拒绝"},
        {"field": "actor", "kw": "U010001"},
        {"field": "actor", "kw": "  王 "},
        {"field": "target", "kw": "CR-0007"},
        {"field": "target", "kw": "%"},  # 不当通配符
        {"action": "查询", "result": "成功", "field": "target", "kw": "QS26102"},
        {"action": "不存在的动作"},
    ],
)
async def test_filters_match_frontend(client, users, authorized, flt):
    got, _ = await page_all(client, size=20, **{"from": D30[0], "to": D30[1]}, **flt)
    assert got == expect(*D30, **flt)


async def test_time_window_inclusive_and_inverted(client, users, authorized):
    t = MOCK[5]["time"]
    r = await client.get(URL, params={"from": t, "to": t})
    assert [x["id"] for x in r.json()["items"]] == [MOCK[5]["id"]]
    r = await client.get(URL, params={"from": H24[1], "to": H24[0]})  # 起 > 止：前端过滤为空，不报错
    assert r.status_code == 200 and r.json() == {"items": [], "nextCursor": None}
    r = await client.get(URL, params={"from": "2026-10-25 15:00", "to": "2026-10-25 16:00"})  # 到分钟也接受
    assert r.json()["items"] == expect("2026-10-25 15:00:00", "2026-10-25 16:00:00")[:20]


async def test_from_to_required_and_validation(client, users, authorized):
    for params in ({}, {"from": H24[0]}, {"to": H24[1]}):
        r = await client.get(URL, params=params)
        assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    r = await client.get(URL, params={"from": H24[0], "to": H24[1], "result": "OK"})
    assert r.status_code == 422
    r = await client.get(URL, params={"from": H24[0], "to": H24[1], "field": "ip"})
    assert r.status_code == 422
    r = await client.get(URL, params={"from": H24[0], "to": H24[1], "size": 0})
    assert r.status_code == 422


async def test_bad_cursor_falls_back_to_first_page(client, users, authorized):
    first = (await client.get(URL, params={"from": H24[0], "to": H24[1]})).json()
    for bad in ("garbage", "c14", "k!!!", "kAAAA"):
        r = await client.get(URL, params={"from": H24[0], "to": H24[1], "cursor": bad})
        assert r.status_code == 200 and r.json() == first


def test_cursor_roundtrip():
    c = AuditCursor(1792915715000123, 42)
    assert AuditCursor.decode(c.encode()) == c
    assert AuditCursor.decode(None) is None and AuditCursor.decode("") is None


async def test_actions_match_frontend(client, users, authorized):
    r = await client.get(URL + "/actions")
    assert r.status_code == 200
    assert r.json() == list(dict.fromkeys(x["action"] for x in MOCK))


async def test_export_returns_job_and_writes_audit(client, users, authorized):
    body = {"from": H24[0], "to": H24[1], "action": "导出", "result": "全部"}
    r = await client.post(URL + "/export", json=body, headers={"X-Forwarded-For": "10.12.40.8, 10.0.0.1"})
    assert r.status_code == 200, r.text
    job = r.json()["jobId"]
    assert job.startswith("EXP-AL")
    row = await users.scalar(select(AuditLog).where(AuditLog.code == job.removeprefix("EXP-")))
    assert row.actor == "王敏（U010001）" and row.action == "导出" and row.ip == "10.12.40.8"
    assert row.target == f"审计日志（{H24[0]} 至 {H24[1]}）"
    assert not row.is_seed and row.created_by == "U010001"
    # 新行出现在列表里（RENGU_FIXED_NOW 2026-10-25 15:42:00 落在近 24 小时内），动作下拉不重复
    items, _ = await page_all(client, size=200, **{"from": H24[0], "to": H24[1], "action": "导出"})
    assert row.code in [x["id"] for x in items] and len(items) == len(expect(*H24, action="导出")) + 1
    acts = (await client.get(URL + "/actions")).json()
    assert len(acts) == len(set(acts))


async def test_export_validation_and_readonly(client, users, authorized):
    r = await client.post(URL + "/export", json={"to": H24[1]})
    assert r.status_code == 422
    r = await client.post(URL + "/export", params={"ver": "v2026.10.2"}, json={"from": H24[0], "to": H24[1]})
    assert r.status_code == 403 and r.json()["code"] == "READONLY"


async def test_insert_audit_and_append_only(users):
    row = await insert_audit(
        users, actor="王敏（U010001）", action="登录", target="本体智能平台", ip="10.0.0.1"
    )
    assert row.id and row.result is AuditResult.success and row.code.startswith("AL")
    row2 = await insert_audit(users, actor="x", action="登录", target="t", ip="1", result="拒绝")
    assert row2.result is AuditResult.denied and row2.code != row.code
    with pytest.raises(DBAPIError, match="只追加"):
        async with users.begin_nested():
            await users.execute(text("UPDATE audit_log SET ip = '0' WHERE id = :i"), {"i": row.id})
    with pytest.raises(DBAPIError, match="只追加"):
        async with users.begin_nested():
            await users.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": row.id})


async def test_requires_login(client, users, authorized):
    r = await client.get(URL, params={"from": H24[0], "to": H24[1]}, headers={"X-Demo-As": "U999999"})
    assert r.status_code == 401
