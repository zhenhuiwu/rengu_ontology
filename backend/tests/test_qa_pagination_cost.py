"""QA真实SQL分页与列表目录成本；不调用LLM。"""

import re

import pytest
from sqlalchemy import event

from app.modules.qa import catalog
from tests.test_qa_api import qa  # noqa: F401

pytestmark = pytest.mark.usefixtures("qa")


async def test_list_catalog_does_not_load_dimension_graph(session, conn):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.lower().split()))

    event.listen(conn.sync_connection, "before_cursor_execute", record)
    try:
        # 列表只需指标名/版本，不能加载无关的维度/对象/属性图。
        metrics, dims = await catalog.directory(session, include_dims=False)
    finally:
        event.remove(conn.sync_connection, "before_cursor_execute", record)
    graphs = [s for s in statements if "from dimension" in s or "from property" in s or "from dim_level" in s]
    assert metrics and dims == []
    assert len(statements) == 2
    assert not graphs, f"directory: {len(statements)} queries, {len(graphs)} unrelated graph queries"


async def test_live_lists_issue_bounded_sql_and_accurate_pages(client, conn):
    endpoints = [
        ("/sessions", "qa_session", {"q": "华东"}),
        ("/tuning/certs", "qa_cert", {"status": "已认证"}),
        ("/tuning/eval/runs", "qa_eval_run", {}),
        ("/tuning/eval/questions", "qa_eval_question", {}),
        ("/tuning/feedbacks", "qa_feedback", {}),
        ("/tuning/freq", "qa_freq_question", {"period": "7"}),
    ]
    for path, table, filters in endpoints:
        full = await client.get("/api/qa/v1" + path, params={**filters, "size": 200})
        assert full.status_code == 200, full.text
        expected = full.json()
        assert 2 < expected["total"] <= 200
        assert len(expected["items"]) == expected["total"]
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany, statements=statements):
            statements.append(" ".join(statement.lower().split()))

        event.listen(conn.sync_connection, "before_cursor_execute", record)
        try:
            response = await client.get("/api/qa/v1" + path, params={**filters, "page": 2, "size": 2})
        finally:
            event.remove(conn.sync_connection, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == expected["total"] and body["page"] == 2 and body["size"] == 2
        assert body["items"] == expected["items"][2:4]
        reads = [sql for sql in statements if re.search(rf"\bfrom {table}\b", sql)]
        assert any("count(" in sql for sql in reads), (path, [sql[:120] for sql in reads])
        data_reads = [sql for sql in reads if "count(" not in sql]
        assert data_reads and all("limit " in sql and "offset " in sql for sql in data_reads), path
        assert not any("from dimension" in sql or "from dim_level" in sql for sql in statements), path
        # Server clamps an out-of-range page rather than returning a fake empty slice.
        response = await client.get("/api/qa/v1" + path, params={**filters, "page": 999, "size": 2})
        body = response.json()
        assert body["page"] == (expected["total"] + 1) // 2
        assert body["items"] == expected["items"][(body["page"] - 1) * 2 :]
