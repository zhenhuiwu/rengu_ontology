"""运行页列表必须SQL分页，翻页不能重新组装整份动作/规则定义目录。"""

from time import perf_counter

from sqlalchemy import event, func, select

from app.common.auth import resolve_user
from app.modules.dynamic.models import ActionRun, Alert
from app.modules.dynamic.rules_models import Rule, Scenario, ScenarioImpact
from tests.dynamic_support import API, dyn  # noqa: F401


async def test_runtime_list_query_budget(client, dyn, engine):  # noqa: F811
    statements = []

    def captured(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", captured)
    try:
        await resolve_user(dyn, "U010001")
        auth_queries = list(statements)
        # Fixed batched auth: user, function grants, object grants, scopes, object identity directory.
        assert len(auth_queries) == 5, auth_queries
        for path, table, business_budget in (
            ("/alerts?status=all", "alert", 7),  # 3 catalog + count/page + batched runs + status groups
            ("/action-runs?tab=records", "action_run", 9),  # catalog + count/page + 2 groups + 2 ID maps
            ("/scenarios", "scenario", 3),  # count/page + batched base releases
        ):
            statements.clear()
            started = perf_counter()
            response = await client.get(API + path + ("&" if "?" in path else "?") + "page=2&size=10")
            assert response.status_code == 200, response.text
            print(f"{path}: {len(statements)} SELECTs, {(perf_counter() - started) * 1000:.1f}ms")
            assert statements[: len(auth_queries)] == auth_queries
            assert len(statements) - len(auth_queries) <= business_budget, statements
            assert any(f"FROM {table}" in sql and "LIMIT" in sql and "OFFSET" in sql for sql in statements)
            assert not any(
                f"FROM {child}" in sql
                for sql in statements
                for child in (
                    "action_param",
                    "submission_criterion",
                    "ontology_edit_rule",
                    "side_effect",
                    "action_type_rule",
                    "rule_hit",
                )
            )
            first = await client.get(API + path + ("&" if "?" in path else "?") + "page=1&size=10")
            assert first.json()["total"] == response.json()["total"]
            assert len(first.json()["items"]) == 10
            assert response.json()["page"] == 2
            assert not (
                {row["id"] for row in first.json()["items"]} & {row["id"] for row in response.json()["items"]}
            )
            statements.clear()
            larger = await client.get(API + path + ("&" if "?" in path else "?") + "page=1&size=20")
            assert larger.status_code == 200, larger.text
            assert len(larger.json()["items"]) > len(response.json()["items"])
            assert statements[: len(auth_queries)] == auth_queries
            assert len(statements) - len(auth_queries) <= business_budget, (
                "Page growth must not add per-row queries"
            )
        statements.clear()
        meta = await client.get(API + "/actions/meta")
        assert meta.status_code == 200
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
        assert response.json()["total"] == await dyn.scalar(select(func.count()).select_from(model))
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
