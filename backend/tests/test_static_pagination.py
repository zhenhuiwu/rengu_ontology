"""静态列表SQL限页与历史/草稿分页边界。"""

import pytest
from sqlalchemy import event, select

from tests.test_static import static_sections, static_seeded  # noqa: F401

P = "/api/ontology/v1"


async def test_cr_diffs_sql_pages_legacy_then_saved_items_and_light_detail(client, static_seeded, engine):  # noqa: F811
    from app.modules.static.models import ChangeRequest

    cr = await make_cr(client, "object", "diff_page_object")
    await save(
        client,
        cr,
        "object",
        "diff_page_object",
        "properties",
        [
            {
                "key": f"p{n:02}",
                "op": "add",
                "after": {"api": f"p{n:02}", "name": f"字段{n}", "type": "string"},
            }
            for n in range(23)
        ],
    )
    record = await static_seeded.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))
    legacy = [
        {
            "key": f"legacy-{n}",
            "type": "对象类型",
            "target": "旧格式说明",
            "field": f"说明{n}",
            "kind": "修改",
            "before": "原",
            "after": "新",
        }
        for n in range(7)
    ]
    record.detail = {**record.detail, "diffs": legacy}
    await static_seeded.flush()
    queries = []

    def capture(_conn, _cursor, sql, _params, _context, _many):
        queries.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        first = await client.get(P + f"/change-requests/{cr}/diffs")
        assert first.status_code == 200, first.text
        assert first.json()["total"] == 30 and first.json()["size"] == 10
        assert first.json()["items"][:7] == legacy
        queries.clear()
        second = await client.get(P + f"/change-requests/{cr}/diffs", params={"page": 2})
        assert second.status_code == 200, second.text
        assert len(second.json()["items"]) == 10 and second.json()["items"][0]["field"] == "p03"
        assert any(
            "UNION ALL" in sql and "FROM cr_item" in sql and "LIMIT" in sql and "OFFSET" in sql
            for sql in queries
        )
        assert any("count(" in sql.lower() and "UNION ALL" in sql for sql in queries)
        assert not any("FROM cr_event" in sql for sql in queries)
        final = await client.get(P + f"/change-requests/{cr}/diffs", params={"page": 99})
        assert final.json()["page"] == 3 and len(final.json()["items"]) == 10
        queries.clear()
        header = await client.get(P + f"/change-requests/{cr}", params={"includeDiffs": False})
        assert header.status_code == 200 and header.json()["detail"]["diffs"] == []
        assert not any("FROM cr_item" in sql for sql in queries)
        complete = await client.get(P + f"/change-requests/{cr}")
        assert len(complete.json()["detail"]["diffs"]) == 30
        assert (await client.get(P + "/change-requests/CR-MISSING/diffs")).status_code == 404
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_current_list_sql_pages_and_focus(client, static_seeded, engine):  # noqa: F811
    queries = []

    def capture(_conn, _cursor, sql, _params, _context, _many):
        queries.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        for path, table in (
            ("object-types", "object_type"),
            ("metrics", "metric"),
            ("dimensions", "dimension"),
            ("knowledge", "knowledge"),
            ("link-types", "link_type"),
        ):
            queries.clear()
            response = await client.get(P + "/" + path, params={"page": 2, "size": 3})
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page"] == 2 and body["size"] == 3 and len(body["items"]) == 3
            assert any(f"FROM {table}" in sql and "LIMIT" in sql and "OFFSET" in sql for sql in queries)
            first = (await client.get(P + "/" + path, params={"page": 1, "size": 3})).json()
            assert first["total"] == body["total"]
            assert {x["id"] for x in first["items"]}.isdisjoint({x["id"] for x in body["items"]})
            focus = body["items"][0]["id"]
            focused = await client.get(P + "/" + path, params={"focusId": focus, "size": 3})
            assert focused.status_code == 200, focused.text
            assert focused.json()["page"] == 2 and focus in [row["id"] for row in focused.json()["items"]]
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_child_pages_match_current_read_semantics(client, static_seeded):  # noqa: F811
    from app.common.context import ViewContext
    from app.modules.static.service import StaticService

    service = StaticService(static_seeded, ViewContext())
    dims = await service._dims_full()
    for d in dims[:3]:
        values = service._value_rows(d)
        response = await client.get(P + f"/dimensions/{d['id']}/values", params={"size": 200})
        assert response.status_code == 200, response.text
        assert response.json()["items"] == values
        expected = service._map_rows(d, dims)
        response = await client.get(P + f"/dimensions/{d['id']}/mappings", params={"size": 200})
        assert response.status_code == 200, response.text
        assert response.json()["items"] == expected
        conflicts = [row for row in expected if row["conflictText"]]
        filtered = await client.get(
            P + f"/dimensions/{d['id']}/mappings", params={"size": 2, "page": 2, "conflict": True}
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["total"] == len(conflicts)


async def make_cr(client, kind, code):
    response = await client.post(
        P + "/change-requests",
        json={
            "title": "真实分页草稿",
            "target": {"kind": kind, "id": code, "isNew": True, "name": "真实分页目标"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def save(client, cr, kind, code, section, rows):
    response = await client.put(
        P + f"/change-requests/{cr}/items",
        json={"target": {"kind": kind, "id": code, "isNew": True}, "section": section, "rows": rows},
    )
    assert response.status_code == 200, response.text


async def test_cr_overlay_pages_new_object_and_dimension(client, static_seeded):  # noqa: F811
    cr = await make_cr(client, "object", "sql_page_object")
    rows = [
        {
            "key": f"p{n:02}",
            "op": "add",
            "after": {
                "api": f"p{n:02}",
                "name": f"分页属性{n}",
                "type": "string",
                "source": f"真实表.col{n}",
            },
        }
        for n in range(11)
    ]
    await save(client, cr, "object", "sql_page_object", "properties", rows)
    page = await client.get(
        P + "/object-types/sql_page_object/properties", params={"crId": cr, "page": 2, "size": 10}
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 11 and [row["api"] for row in page.json()["items"]] == ["p10"]
    filtered = await client.get(
        P + "/object-types/sql_page_object/properties", params={"crId": cr, "kw": "属性10", "page": 99}
    )
    assert filtered.status_code == 200 and filtered.json()["total"] == 1 and filtered.json()["page"] == 1
    mapped = await client.get(
        P + "/object-types/sql_page_object/mappings", params={"crId": cr, "page": 2, "size": 10}
    )
    assert mapped.status_code == 200 and mapped.json()["items"][0]["col"] == "真实表.col10", mapped.text
    assert (await client.get(P + "/object-types/sql_page_object/properties")).status_code == 404
    assert (
        await client.get(P + "/object-types/sql_page_object/properties", params={"crId": "missing"})
    ).status_code == 404
    assert (
        await client.get(
            P + "/object-types/sql_page_object/properties", params={"crId": cr, "ver": "v2026.10.1"}
        )
    ).status_code == 403
    cr2 = await make_cr(client, "dimension", "sql_page_dimension")
    values = [
        {
            "key": f"层:{n:02}",
            "op": "add",
            "after": {
                "code": f"{n:02}",
                "name": f"分页取值{n}",
                "level": "层",
                "status": "启用",
                "synonyms": [],
            },
        }
        for n in range(11)
    ]
    await save(client, cr2, "dimension", "sql_page_dimension", "values", values)
    page = await client.get(
        P + "/dimensions/sql_page_dimension/values", params={"crId": cr2, "page": 2, "size": 10}
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 11 and page.json()["items"][0]["code"] == "10"
    # 删除、更新也先作用在完整候选集，再过滤/计数。
    changed = [
        *values[:-1],
        {**values[-1], "op": "update", "after": {**values[-1]["after"], "name": "改名取值"}},
    ]
    await save(client, cr2, "dimension", "sql_page_dimension", "values", changed)
    result = await client.get(
        P + "/dimensions/sql_page_dimension/values", params={"crId": cr2, "q": "改名", "page": 9}
    )
    assert (
        result.status_code == 200
        and result.json()["total"] == 1
        and result.json()["items"][0]["name"] == "改名取值"
    ), result.text


async def test_saved_property_order_and_bad_rows_are_safe(client, static_seeded):  # noqa: F811
    code = "ordered_cr_object"
    cr = await make_cr(client, "object", code)
    rows = [
        {"key": f"p{n}", "op": "add", "after": {"name": f"属性{n}", "type": "string", "order": 2 - n}}
        for n in range(3)
    ]
    await save(client, cr, "object", code, "properties", rows)
    response = await client.get(P + f"/object-types/{code}/properties", params={"crId": cr, "size": 1})
    assert response.status_code == 200 and response.json()["items"][0]["api"] == "p2", response.text
    for after in ({"order": [0]}, []):
        await save(client, cr, "object", code, "properties", [{"key": "bad", "op": "add", "after": after}])
        response = await client.get(P + f"/object-types/{code}/properties", params={"crId": cr})
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_FAILED", response.text


@pytest.mark.parametrize("value", [None, {}, "not-array", 4])
async def test_json_page_non_array_is_safe(client, static_seeded, value):  # noqa: F811
    from app.modules.static.models import Knowledge

    row = await static_seeded.scalar(select(Knowledge).limit(1))
    row.refs = value
    await static_seeded.flush()
    response = await client.get(P + f"/knowledge/{row.code}/refs")
    assert response.status_code == 200 and response.json()["items"] == [] and response.json()["total"] == 0, (
        response.text
    )


async def test_history_pages_do_not_read_current_definition_tables(client, static_seeded, engine):  # noqa: F811
    from app.modules.static.models import Metric, ObjectType

    row = await static_seeded.scalar(select(ObjectType).where(ObjectType.code == "store"))
    row.display_name = "当前新增名称不得穿越"
    metric = await static_seeded.scalar(select(Metric).where(Metric.code == "gmv"))
    metric.formula = "当前新增公式不得穿越"
    await static_seeded.flush()
    queries = []

    def capture(_conn, _cursor, sql, _params, _context, _many):
        queries.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        for path in (
            "/object-types",
            "/metrics",
            "/dimensions",
            "/knowledge",
            "/link-types",
            "/dimensions/org/values",
            "/dimensions/org/mappings",
        ):
            response = await client.get(P + path, params={"ver": "v2026.10.1", "page": 2, "size": 3})
            assert response.status_code in (200, 404), response.text
            assert "当前新增" not in response.text
        # Role authorization reads identity columns; page definitions still come only from snapshots.
        identities = [
            sql
            for sql in queries
            if "FROM object_type" in sql
            and sql.split("FROM object_type", 1)[0].strip()
            in (
                "SELECT object_type.code, object_type.api_name, object_type.display_name",
                "SELECT object_type.id, object_type.code, object_type.api_name, object_type.display_name",
            )
        ]
        assert all("WHERE object_type.status" in sql for sql in identities)
        # Governance loads the current workspace's object codes, never definitions.
        definition_queries = [
            sql
            for sql in queries
            if sql not in identities and sql != "SELECT code FROM object_type WHERE ontology_id = $1"
        ]
        forbidden = [
            sql
            for sql in definition_queries
            if any(
                f"FROM {table} " in sql or f"FROM {table}\n" in sql
                for table in (
                    "object_type",
                    "property",
                    "metric",
                    "dimension",
                    "dim_value",
                    "dim_mapping",
                    "knowledge",
                    "link_type",
                )
            )
        ]
        assert not forbidden, forbidden
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_saved_synonyms_and_mapping_conflicts_use_complete_database(client, static_seeded):  # noqa: F811
    from app.modules.static.models import DimValue, Knowledge

    known = await static_seeded.scalar(select(Knowledge).limit(1))
    cr = await make_cr(client, "knowledge", "K-SQL-PAGE")
    await save(
        client,
        cr,
        "knowledge",
        "K-SQL-PAGE",
        "content",
        [{"key": "synonyms", "op": "update", "after": [known.name]}],
    )
    response = await client.get(P + "/knowledge/synonym-conflicts", params={"crId": cr})
    assert response.status_code == 200, response.text
    assert any(
        row["word"] == known.name and "K-SQL-PAGE" in row["ids"] and known.code in row["ids"]
        for row in response.json()["conflicts"]
    )
    await save(
        client,
        cr,
        "knowledge",
        "K-SQL-PAGE",
        "content",
        [{"key": "synonyms", "op": "update", "after": [["bad"]]}],
    )
    assert (await client.get(P + "/knowledge/synonym-conflicts", params={"crId": cr})).status_code == 422
    word = await static_seeded.scalar(select(DimValue.name).where(DimValue.status == "enabled").limit(1))
    cr2 = await make_cr(client, "dimension", "sql_conflict_dimension")
    await save(
        client,
        cr2,
        "dimension",
        "sql_conflict_dimension",
        "values",
        [
            {
                "key": "层:1",
                "op": "add",
                "after": {
                    "code": "1",
                    "name": "本维度真实值",
                    "level": "层",
                    "status": "启用",
                    "synonyms": [word],
                },
            }
        ],
    )
    response = await client.get(
        P + "/dimensions/sql_conflict_dimension/mappings",
        params={"crId": cr2, "conflict": True, "size": 1, "page": 9},
    )
    assert response.status_code == 200, response.text
    assert (
        response.json()["total"] == 1
        and response.json()["items"][0]["phrase"] == word
        and response.json()["items"][0]["conflictText"]
    )


async def test_cr_sql_pages_preserve_complete_compatibility(client, static_seeded):  # noqa: F811
    complete = await client.get(P + "/change-requests", params={"full": True})
    assert complete.status_code == 200 and isinstance(complete.json(), list)
    rows = complete.json()
    page = await client.get(P + "/change-requests", params={"full": True, "page": 2, "size": 3})
    assert (
        page.status_code == 200 and page.json()["total"] == len(rows) and page.json()["items"] == rows[3:6]
    ), page.text
    focus = await client.get(P + "/change-requests", params={"focusId": rows[4]["id"], "size": 3})
    assert focus.status_code == 200 and focus.json()["page"] == 2, focus.text
    found = await client.get(P + "/change-requests", params={"kw": rows[0]["id"], "page": 9, "size": 3})
    assert found.status_code == 200 and found.json()["total"] == 1 and found.json()["page"] == 1


async def test_workflow_is_complete_light_and_merges_real_targets(client, static_seeded, engine):  # noqa: F811
    cr = await make_cr(client, "object", "workflow_real_object")
    await save(
        client,
        cr,
        "object",
        "workflow_real_object",
        "properties",
        [{"key": "property", "op": "add", "after": {"name": "实际属性", "type": "string"}}],
    )
    queries = []

    def capture(_conn, _cursor, sql, _params, _context, _many):
        queries.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        response = await client.get(
            P + "/change-requests", params={"workflow": True, "full": True, "page": 2, "size": 1}
        )
        assert response.status_code == 200 and isinstance(response.json(), list), response.text
        rows = response.json()
        assert len(rows) == 17
        row = next(row for row in rows if row["id"] == cr)
        assert (
            "object:workflow_real_object" in row["detail"]["touches"]
            and row["targets"][0]["id"] == "workflow_real_object"
        )
        assert all(set(row["detail"]) == {"touches"} and "events" not in row for row in rows)
        assert not any("FROM cr_item" in sql or "FROM cr_event" in sql for sql in queries)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_usage_views_metrics_and_cr_sql_pages(client, static_seeded):  # noqa: F811
    from app.modules.static.models import CrTarget, Metric, ObjectType, ObjectView, Property

    parent = await static_seeded.scalar(
        select(ObjectType)
        .where(
            ~ObjectType.code.in_(select(CrTarget.target_id).where(CrTarget.target_kind == "object")),
            ObjectType.id.in_(select(ObjectView.object_type_id)),
        )
        .order_by(ObjectType.id)
    )
    cr = await make_cr(client, "object", parent.code)
    view = await static_seeded.scalar(
        select(ObjectView).where(ObjectView.object_type_id == parent.id).limit(1)
    )
    prop = next(iter(view.props), None)
    if prop is None:
        prop = await static_seeded.scalar(
            select(Property.api_name).where(Property.object_type_id == parent.id).limit(1)
        )
        view.props = [prop]
        await static_seeded.flush()
    await save(
        client,
        cr,
        "object",
        parent.code,
        "properties",
        [{"key": prop, "op": "update", "after": {"desc": "真实已保存改动"}}],
    )
    response = await client.get(
        P + f"/object-types/{parent.code}/usage",
        params={"kind": "view", "crId": cr, "affectedOnly": True, "size": 1, "page": 2},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] >= 1 and all(
        prop in row["affectedProps"] for row in response.json()["items"]
    )
    empty = await client.get(
        P + f"/object-types/{parent.code}/usage", params={"kind": "view", "affectedOnly": True}
    )
    assert empty.status_code == 200 and empty.json()["total"] == 0
    response = await client.get(
        P + f"/object-types/{parent.code}/usage",
        params={"kind": "cr", "filter": "进行中", "q": "真实分页", "size": 1},
    )
    assert response.status_code == 200 and any(row["cr"]["id"] == cr for row in response.json()["items"]), (
        response.text
    )
    for code in ("order", "order_line", "store"):
        response = await client.get(
            P + f"/object-types/{code}/usage", params={"kind": "metric", "size": 2, "page": 2}
        )
        assert response.status_code == 200, response.text
        assert all(code in row["base"] or row["via"] for row in response.json()["items"])
    m = await static_seeded.scalar(select(Metric).where(Metric.code == "gmv"))
    m.formula, m.dims = "不能识别的真实定义", []
    await static_seeded.flush()
    response = await client.get(
        P + "/object-types/order_line/usage", params={"kind": "metric", "q": m.display_name}
    )
    assert response.status_code == 200 and response.json()["total"] == 0, response.text
