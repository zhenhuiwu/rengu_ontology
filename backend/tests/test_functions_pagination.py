"""SQL-scoped metadata and stable pages including a single per-function draft version."""

import pytest
from sqlalchemy import event, select

from app.core import clock
from app.modules.admin.models import SysUser
from app.modules.functions.models import FnParam, FnUse, FnVersion, Function
from app.modules.static.models import CrItem
from tests.test_functions import B, create, functions  # noqa: F401


@pytest.fixture
async def pages(functions):  # noqa: F811
    owner = await functions.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    models = []
    for index in range(23):
        fn = Function(
            code=f"FN-PAGE-{index:03}",
            name=f"paged_{index}",
            title="literal%_function" if index == 0 else f"函数{index}",
            runtime="受限纯计算表达式",
            status="disabled" if index > 20 else "published",
            owner_id=owner,
            timeout_s=5,
            description="",
            code_text=f"def paged_{index}(value):\n    return value",
            sample_in={},
            sample_out=None,
        )
        functions.add(fn)
        await functions.flush()
        functions.add_all(
            [
                FnParam(function_id=fn.id, direction="in", seq=0, name="value", type="Any", desc=""),
                FnUse(function_id=fn.id, kind="规则", name=f"引用{index}"),
            ]
        )
        versions = ["v1.2.0", "v1.10.0"] + ([f"v1.0.{n}" for n in range(23)] if index == 0 else [])
        functions.add_all(
            [
                FnVersion(
                    function_id=fn.id,
                    ver=ver,
                    at=clock.now(),
                    author_id=owner,
                    note=ver,
                    snapshot={"notNeededForRead": "x" * 1000},
                )
                for ver in versions
            ]
        )
        models.append(fn)
    await functions.flush()
    return functions, models


async def test_current_sql_pages_and_detail_children_are_scoped(pages, client):
    session, _ = pages
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append((statement, parameters))

    event.listen(session.bind.sync_engine, "before_cursor_execute", capture)
    try:
        response = await client.get(f"{B}/functions", params={"page": 2})
        assert response.status_code == 200, response.text
        second = response.json()
        assert (second["total"], second["page"], second["size"]) == (23, 2, 10)
        assert [row["id"] for row in second["items"]] == [f"FN-PAGE-{n:03}" for n in range(10, 20)]
        assert all(
            row["version"] == "v1.10.0" and len(row["inputs"]) == len(row["usedBy"]) == 1
            for row in second["items"]
        )
        for table in ("fn_param", "fn_use"):
            statements = [(sql, args) for sql, args in queries if f"FROM {table}" in sql]
            assert statements and all(
                f"WHERE {table}.function_id IN" in sql and len(args) == 10 for sql, args in statements
            )
        assert any("count(" in sql and "FROM function" in sql for sql, _ in queries)
        assert any("LIMIT" in sql and "OFFSET" in sql for sql, _ in queries)
        assert all("fn_version.snapshot" not in sql for sql, _ in queries)
        queries.clear()
        detail = (await client.get(f"{B}/functions/FN-PAGE-000")).json()
        assert detail["version"] == "v1.10.0" and detail["inputs"][0]["name"] == "value"
        assert all(len(args) == 1 for sql, args in queries if "FROM fn_param" in sql or "FROM fn_use" in sql)
        assert any("function.code =" in sql for sql, _ in queries)
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", capture)
    last = (await client.get(f"{B}/functions", params={"page": 999})).json()
    assert last["page"] == 3 and len(last["items"]) == 3
    literal = (await client.get(f"{B}/functions", params={"kw": "%_"})).json()
    assert literal["total"] == 1 and literal["items"][0]["id"] == "FN-PAGE-000"
    disabled = (await client.get(f"{B}/functions", params={"status": "已停用"})).json()
    assert disabled["total"] == 2
    assert (await client.get(f"{B}/functions?status=%00")).status_code == 422


async def test_history_sql_page_with_one_draft_and_clamp(pages, client):
    _, models = pages
    fn = models[0]
    saved = await client.put(
        f"{B}/functions/{fn.code}",
        json={"title": "待审函数", "code": fn.code_text, "lockVersion": fn.lock_version},
    )
    assert saved.status_code == 200, saved.text
    first = (await client.get(f"{B}/functions/{fn.code}/versions")).json()
    second = (await client.get(f"{B}/functions/{fn.code}/versions", params={"page": 2})).json()
    assert first["total"] == second["total"] == 26 and first["size"] == second["size"] == 10
    assert [r["ver"] for r in first["items"]][:3] == ["草稿", "v1.10.0", "v1.2.0"]
    assert [r["status"] for r in first["items"]][:3] == ["变更中", "当前", "历史"]
    assert all(r["status"] == "历史" for r in second["items"])
    assert not {r["ver"] for r in first["items"]} & {r["ver"] for r in second["items"]}
    last = (await client.get(f"{B}/functions/{fn.code}/versions", params={"page": 999})).json()
    assert last["page"] == 3 and len(last["items"]) == 6


async def test_new_draft_union_filters_and_invalid_cr_exclusion(pages, client):
    session, _ = pages
    created = await create(client, name="new_paged", code="def new_paged(value):\n    return value")
    all_rows = (await client.get(f"{B}/functions")).json()
    assert all_rows["total"] == 24 and all_rows["items"][0]["id"] == created["id"]
    drafts = (await client.get(f"{B}/functions", params={"status": "草稿", "kw": "NEW_PAGED"})).json()
    assert drafts["total"] == 1 and drafts["items"][0]["id"] == created["id"]
    history = (await client.get(f"{B}/functions/{created['id']}/versions", params={"page": 99})).json()
    assert history["total"] == history["page"] == 1 and history["items"][0]["ver"] == "草稿"
    row = await session.scalar(
        select(CrItem).where(CrItem.target_id == created["id"], CrItem.item_key == "code")
    )
    row.after = "def mismatched_name(value):\n    return value"
    await session.flush()
    assert (await client.get(f"{B}/functions")).json()["total"] == 23
    assert (await client.get(f"{B}/functions/{created['id']}")).status_code == 404
    assert (await client.get(f"{B}/functions/{created['id']}/versions")).status_code == 404
