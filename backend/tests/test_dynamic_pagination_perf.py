"""运行页列表必须SQL分页，翻页不能重新组装整份动作/规则定义目录。"""

from time import perf_counter

from sqlalchemy import event, func, select

from app.common.auth import resolve_user
from app.modules.dynamic.models import ActionRun, Alert
from app.modules.dynamic.row_scope import scoped_instances
from app.modules.dynamic.rules_models import Rule, Scenario, ScenarioImpact
from tests.dynamic_support import API, dyn  # noqa: F401
from tests.test_use_ops_runtime import runtime  # noqa: F401


async def test_runtime_list_query_budget(client, runtime, engine):  # noqa: F811
    statements = []

    def captured(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", captured)
    try:
        await resolve_user(runtime[0], "U010001")
        auth_queries = list(statements)
        assert any("ontology_member" in sql for sql in auth_queries)
        print(f"Fixed current-member auth SELECTs: {len(auth_queries)}")
        for path, table in (("/alerts?status=all", "alert"), ("/action-runs?tab=pending", "action_run")):
            budgets = []
            first_ids = None
            for size in (5, 13):
                statements.clear()
                started = perf_counter()
                response = await client.get(API + path + f"&page=1&size={size}")
                assert response.status_code == 200, response.text
                data = response.json()
                assert data["total"] == 13 and len(data["items"]) == size
                ids = {row["id"] for row in data["items"]}
                if first_ids is not None:
                    assert first_ids <= ids
                first_ids = ids
                budgets.append(len(statements))
                assert any(
                    f"FROM {table}" in sql and "LIMIT" in sql and "OFFSET" in sql for sql in statements
                )
                # Public references require the current page's definitions, once per child table, not per row.
                for child in (
                    "action_param",
                    "submission_criterion",
                    "ontology_edit_rule",
                    "side_effect",
                    "action_type_rule",
                ):
                    assert sum(f"FROM {child}" in sql for sql in statements) <= 1
                assert not any("FROM rule_hit" in sql for sql in statements)
                print(
                    f"{path} size={size}: {len(statements)} SELECTs, "
                    f"{(perf_counter() - started) * 1000:.1f}ms"
                )
            assert budgets[1] <= budgets[0] + 2, ("Page growth added per-row queries", budgets)
        statements.clear()
        assert (await client.get(API + "/actions/meta")).status_code == 200
        assert not any(
            f"FROM {child}" in sql for sql in statements for child in ("action_param", "rule_hit", "rule ")
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", captured)


async def test_filters_totals_defaults_and_impacts_sql(client, dyn, engine):  # noqa: F811
    for path, model, params in (
        ("/alerts", Alert, {"status": "all"}),
        ("/action-runs", ActionRun, {}),
        ("/scenarios", Scenario, {}),
    ):
        response = await client.get(API + path, params=params)
        assert response.status_code == 200, response.text
        assert response.json()["size"] == 10
        expected = select(model.id)
        if model in (Alert, ActionRun):
            expected = await scoped_instances(
                expected, await resolve_user(dyn, "U010001"), model.obj_kind, model.obj_id
            )
        assert response.json()["total"] == await dyn.scalar(
            select(func.count()).select_from(expected.subquery())
        )
    for path in ("/alerts", "/action-runs", "/scenarios"):
        empty = await client.get(API + path, params={"kw": "不存在的真实过滤值", "page": 200, "size": 10})
        assert empty.json()["items"] == [] and empty.json()["total"] == 0 and empty.json()["page"] == 1
    bad = await client.get(API + "/scenarios", params={"status": "不存在"})
    assert bad.status_code == 422
    scenario = await dyn.scalar(select(Scenario).where(Scenario.code == "SCN-0018"))
    total = await dyn.scalar(
        select(func.count()).select_from(ScenarioImpact).where(ScenarioImpact.scenario_id == scenario.id)
    )
    queries = []

    def captured(_conn, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", captured)
    try:
        pages = [
            await client.get(API + "/scenarios/SCN-0018/impacts", params={"page": page, "size": 2})
            for page in (1, 2)
        ]
        assert any("FROM scenario_impact" in sql and "LIMIT" in sql and "OFFSET" in sql for sql in queries)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", captured)
    assert all(response.status_code == 200 and response.json()["total"] == total for response in pages)
    assert {(row["sku"], row["storeId"]) for row in pages[0].json()["items"]}.isdisjoint(
        {(row["sku"], row["storeId"]) for row in pages[1].json()["items"]}
    )
    rule = await dyn.scalar(select(Rule).limit(1))
    rule.name = "数据库真实筛选项"
    await dyn.flush()
    meta = await client.get(API + "/alerts/meta")
    assert {"id": rule.code, "name": "数据库真实筛选项"} in meta.json()["rules"]
    denied = await client.get(API + "/alerts/meta", headers={"X-Demo-As": "U010006"})
    assert denied.status_code == 403
