"""W9 切换口径 / W10 结果表翻页 / W11 导出 / W12 赞踩（工作台回答卡联调）。"""

import csv
import io
import zipfile

import pytest
from sqlalchemy import select

from app.modules.qa.models import QaTurn
from tests import qa_page_test_support
from tests.function_access_support import grant_fixture_roles
from tests.qa_access_support import seed_qa_access

qa_workspace = qa_page_test_support.qa_workspace
users = qa_page_test_support.users

B = "/api/qa/v1"
STORE, MAIN = "T-01", "T-04"


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await grant_fixture_roles(users, {"U010001": "R-01", "U010006": "R-05"})
    await users.flush()
    return client


async def _turn(qa, tid: str) -> dict:
    return next(t for t in (await qa.get(f"{B}/sessions/S0001")).json()["turns"] if t["id"] == tid)


async def _fb_total(qa) -> tuple[int, dict]:
    d = (await qa.get(f"{B}/tuning/feedbacks?size=1")).json()
    return d["total"], d["items"][0]


async def test_recalc_persists_caliber(qa):
    r = await qa.post(f"{B}/turns/{MAIN}:recalc", json={"caliber": "both"})
    assert r.status_code == 200, r.text
    assert r.json()["caliber"] == "both" and r.json()["card"] == "main"
    assert (await _turn(qa, MAIN))["answer"]["caliber"] == "both"
    assert (await qa.post(f"{B}/turns/{MAIN}:recalc", json={"caliber": "x"})).status_code == 422
    # 只有主回答卡有口径澄清条
    assert (await qa.post(f"{B}/turns/{STORE}:recalc", json={"caliber": "in"})).status_code == 422


async def test_vote_down_creates_feedback_and_cancel_removes(qa):
    n0, _ = await _fb_total(qa)
    r = await qa.put(f"{B}/turns/{MAIN}/vote", json={"vote": "down"})
    assert r.status_code == 200 and r.json() == {"id": MAIN, "vote": "down"}
    n1, top = await _fb_total(qa)
    assert n1 == n0 + 1 and top["status"] == "待处理" and top["userId"] == "U010001"
    assert top["question"] == (await _turn(qa, MAIN))["question"]
    assert (await _turn(qa, MAIN))["vote"] == "down"
    # 再踩一次不重复生成
    await qa.put(f"{B}/turns/{MAIN}/vote", json={"vote": "down"})
    assert (await _fb_total(qa))[0] == n1
    # 改成赞：撤掉这一轮的待处理反馈
    r = await qa.put(f"{B}/turns/{MAIN}/vote", json={"vote": "up"})
    assert r.json()["vote"] == "up" and (await _fb_total(qa))[0] == n0
    await qa.put(f"{B}/turns/{MAIN}/vote", json={"vote": ""})
    assert "vote" not in await _turn(qa, MAIN)


async def test_turn_writes_readonly_and_owner(qa):
    for path, method, body in (
        (f"{B}/turns/{MAIN}/vote", "put", {"vote": "up"}),
        (f"{B}/turns/{MAIN}:recalc", "post", {"caliber": "in"}),
    ):
        r = await getattr(qa, method)(f"{path}?ver=v2026.10.2", json=body)
        assert r.status_code == 403 and r.json()["code"] == "READONLY"
        r = await getattr(qa, method)(path, json=body, headers={"X-Demo-As": "U010006"})
        assert r.status_code == 404
    assert (await qa.get(f"{B}/turns/{STORE}/result", headers={"X-Demo-As": "U010006"})).status_code == 404


async def _save_table(session, items=None):
    table = {
        "columns": [
            {"key": "store", "title": "数据库门店", "align": "left"},
            {"key": "sales", "title": "已存销售额", "align": "right"},
        ],
        "items": items
        if items is not None
        else [{"store": f"库中门店{i}", "sales": i * 3} for i in range(13)],
        "summary": "数据库已存结果",
    }
    turn = await session.scalar(select(QaTurn).where(QaTurn.code == STORE))
    turn.snapshot = {"answer": {"card": "table", "table": table}}
    await session.flush()
    return table


async def test_legacy_store_without_saved_table_cannot_read_or_export(qa):
    for suffix in ("result", "export?format=csv", "export?format=xlsx"):
        response = await qa.get(f"{B}/turns/{STORE}/{suffix}")
        assert response.status_code == 422
        assert response.json()["code"] == "TURN_NO_RESULT"


async def test_store_result_pages_and_summary_over_all_rows(qa, session):
    table = await _save_table(session)
    r = await qa.get(f"{B}/turns/{STORE}/result?page=2&size=10")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total"] == 13 and d["page"] == 2
    assert d["items"] == table["items"][10:] and d["columns"] == table["columns"]
    assert d["summary"] == {"text": "数据库已存结果"}
    assert (await qa.get(f"{B}/turns/{STORE}/result?page=9&size=10")).json()["page"] == 2
    assert (await qa.get(f"{B}/turns/{STORE}/result?ver=v2026.10.2")).status_code == 200
    assert (await qa.get(f"{B}/turns/{MAIN}/result")).status_code == 422


async def test_export_csv_and_xlsx(qa, session):
    table = await _save_table(session)
    r = await qa.get(f"{B}/turns/{STORE}/export?format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "filename*=UTF-8''" in r.headers["content-disposition"] and r.headers[
        "content-disposition"
    ].endswith(".csv")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0] == [c["title"] for c in table["columns"]] and len(rows) == 14
    assert rows[1] == ["库中门店0", "0"]
    r = await qa.get(f"{B}/turns/{STORE}/export")
    assert r.status_code == 200 and r.headers["content-disposition"].endswith(".xlsx")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "xl/worksheets/sheet1.xml" in z.namelist()
    assert z.read("xl/worksheets/sheet1.xml").decode().count("<row ") == 14


async def test_saved_empty_result_keeps_header_and_malformed_table_is_unavailable(qa, session):
    await _save_table(session, [])
    response = await qa.get(f"{B}/turns/{STORE}/result")
    assert response.status_code == 200 and response.json()["total"] == 0
    response = await qa.get(f"{B}/turns/{STORE}/export?format=csv")
    assert list(csv.reader(io.StringIO(response.content.decode("utf-8-sig")))) == [
        ["数据库门店", "已存销售额"]
    ]
    turn = await session.scalar(select(QaTurn).where(QaTurn.code == STORE))
    turn.snapshot = {"answer": {"card": "table", "table": {"columns": [{"key": "broken"}], "items": [{}]}}}
    await session.flush()
    for suffix in ("result", "export"):
        response = await qa.get(f"{B}/turns/{STORE}/{suffix}")
        assert response.status_code == 422 and response.json()["code"] == "TURN_NO_RESULT"


async def test_export_forbidden_in_preview(qa):
    r = await qa.get(f"{B}/turns/{STORE}/export?previewAs=U010003")
    assert r.status_code == 403 and r.json()["code"] == "EXPORT_FORBIDDEN"
    assert (await qa.get(f"{B}/turns/{MAIN}/export")).status_code == 422
