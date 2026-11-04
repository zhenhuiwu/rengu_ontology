"""Pure policy and SQL guard assertions, without database, HTTP or pytest."""

import ast
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import sqlglot
from pydantic import ValidationError
from sqlalchemy import Column, Integer, MetaData, String, Table, select

from app.core.errors import Forbidden
from app.modules.governance import query_guard
from app.modules.governance.function_edges import add_function_edges
from app.modules.governance.policy import decide, effective_policies, enforce, policy_roles
from app.modules.governance.schemas import PolicyDefinition
from app.modules.static.lineage import identity


def policy_checks():
    source, prop, metric, action, external = [
        identity(kind, "x") for kind in ("source", "property", "metric", "action", "external")
    ]
    graph = {
        "nodes": [{"id": key} for key in (source, prop, metric, action, external)],
        "edges": [
            {"source": a, "target": b}
            for a, b in zip((source, prop, metric, action), (prop, metric, action, external), strict=True)
        ],
        "unresolved": [],
    }
    body = {
        "classes": [{"code": "private", "name": "私有"}],
        "levels": [{"code": "low", "name": "低"}, {"code": "high", "name": "高"}],
        "purposes": [{"code": "analysis", "name": "分析"}, {"code": "service", "name": "服务"}],
        "grants": [
            {
                "role": "R",
                "purposes": ["analysis"],
                "classes": ["private"],
                "level": "high",
                "operations": ["read", "filter", "aggregate", "export", "external", "execute", "invoke"],
                "unmask": False,
            }
        ],
        "resources": [
            {
                "resource": source,
                "classes": ["private"],
                "level": "high",
                "purposes": ["analysis"],
                "mask": "redact",
            }
        ],
    }
    definition = PolicyDefinition.model_validate(body).model_dump(mode="json")
    original = deepcopy(definition)
    states = effective_policies(definition, graph)
    for key in (source, prop, metric, action, external):
        assert states[key]["classes"] == ["private"] and states[key]["level"] == "high"
        assert not decide(definition, states, ["R"], key, "read", None)["allowed"]
        assert not decide(definition, states, ["R"], key, "read", "service")["allowed"]
        assert not decide(definition, states, ["other"], key, "read", "analysis")["allowed"]
        assert decide(definition, states, ["R"], key, "read", "analysis")["mask"] == "redact"
        for op in ("filter", "aggregate", "export", "external"):
            assert not decide(definition, states, ["R"], key, op, "analysis")["allowed"]
    assert definition == original
    assert decide({}, {}, [], source, "read", None)["allowed"]
    assert not decide(definition, states, ["R"], "unknown", "read", "analysis")["allowed"]
    plain = deepcopy(definition)
    plain["grants"][0]["unmask"] = True
    assert decide(plain, states, ["R"], external, "external", "analysis")["allowed"]
    assert decide(plain, states, ["R"], prop, "read", "analysis")["mask"] == "none"
    insufficient = deepcopy(definition)
    insufficient["grants"][0]["level"] = "low"
    assert not decide(insufficient, states, ["R"], prop, "read", "analysis")["allowed"]
    unknown_graph = deepcopy(graph)
    unknown_graph["unresolved"] = [{"node": prop}]
    assert effective_policies(definition, unknown_graph)[external]["unresolved"]
    attested = deepcopy(definition)
    attested["resources"].append(
        {"resource": prop, "purposes": ["analysis"], "dependencies_complete": True, "reason": "已核实"}
    )
    assert not effective_policies(attested, unknown_graph)[external]["unresolved"]
    narrowed = deepcopy(attested)
    narrowed["resources"][1]["purposes"] = ["service"]
    assert not effective_policies(narrowed, graph)[external]["purposes"]
    downgrade = {"from_level": "high", "to_level": "low", "remove_classes": ["private"]}
    lowered = deepcopy(attested)
    lowered["downgrades"] = [downgrade]
    lowered["resources"][1]["downgrade"] = downgrade
    lower_states = effective_policies(lowered, graph)
    assert lower_states[prop]["level"] == "low" and not lower_states[prop]["classes"]
    lowered["downgrades"] = []
    assert effective_policies(lowered, graph)[external]["unresolved"]
    cyclic = deepcopy(graph)
    cyclic["edges"].append({"source": action, "target": metric})
    assert effective_policies(definition, cyclic)[external]["level"] == "high"
    for malformed in (
        {"resources": [{"resource": source, "classes": ["missing"]}]},
        {"resources": [{"resource": source, "dependencies_complete": True}]},
        {"classes": [{"code": "x", "name": "X"}, {"code": "x", "name": "X"}]},
    ):
        try:
            PolicyDefinition.model_validate(malformed)
        except ValidationError:
            pass
        else:
            raise AssertionError("invalid policy accepted")


async def query_checks():
    table = Table(
        "rows", MetaData(), Column("id", Integer), Column("secret", String), Column("public", String)
    )
    mapping = SimpleNamespace(
        object_type="OBJ", table=table, columns={"secret": table.c.secret, "public": table.c.public}
    )
    calls = []

    async def recorder(_session, _user, resources, operation="read", **kwargs):
        calls.append((operation, set(resources), kwargs))
        return {r: {"mask": "none"} for r in resources}

    columns = {("OBJ", name): {identity("property", "OBJ", name)} for name in ("secret", "public")}
    computed = SimpleNamespace(object_type="OBJ", columns={"secret": (table.c.public + "x").label("secret")})
    assert (
        identity("property", "OBJ", "secret")
        in query_guard.computed_resources(columns, [computed])[("OBJ", "public")]
    )
    with (
        patch.object(query_guard, "load_definition", AsyncMock(return_value={"resources": [1]})),
        patch.object(query_guard, "property_resources", AsyncMock(return_value=columns)),
        patch.object(query_guard, "enforce", recorder),
    ):
        for sql in (
            "SELECT secret FROM rows",
            "SELECT * FROM rows",
            "SELECT r.* FROM rows r",
            "SELECT public FROM rows WHERE secret = $1",
            "SELECT SUM(secret) FROM rows",
            "SELECT public FROM rows GROUP BY public ORDER BY secret",
            "WITH s AS (SELECT secret FROM rows) SELECT * FROM s",
        ):
            calls.clear()
            await query_guard.check_sql(
                None, None, sqlglot.parse_one(sql, read="postgres"), {"rows": mapping}
            )
            assert any(identity("property", "OBJ", "secret") in resources for _, resources, _ in calls), sql
            if "WHERE" in sql or "ORDER" in sql:
                assert any(
                    op == "filter" and identity("property", "OBJ", "secret") in rs for op, rs, _ in calls
                )
            if "SUM" in sql:
                assert any(
                    op == "aggregate" and identity("property", "OBJ", "secret") in rs for op, rs, _ in calls
                )
        calls.clear()
        await query_guard.check_sql(
            None, None, sqlglot.parse_one("SELECT COUNT(*) FROM rows"), {"rows": mapping}
        )
        assert not any(identity("property", "OBJ", "secret") in rs for _, rs, _ in calls)

        async def masked(_session, _user, resources, *_args, **_kwargs):
            return {r: {"mask": "null"} for r in resources}

        with patch.object(query_guard, "enforce", masked):
            try:
                await query_guard.check_sql(
                    None, None, sqlglot.parse_one("SELECT * FROM rows"), {"rows": mapping}
                )
            except Forbidden:
                pass
            else:
                raise AssertionError("raw masked SQL accepted")
        token = query_guard.request_session.set({"active": False, "session": None})
        try:
            try:
                await query_guard.check_statement(select(table.c.secret), None, "OBJ", {})
            except Forbidden:
                pass
            else:
                raise AssertionError("closed context accepted")
        finally:
            query_guard.request_session.reset(token)
    session = SimpleNamespace(
        info={"ontology_id": 1}, scalars=AsyncMock(return_value=["R"]), scalar=AsyncMock(return_value=None)
    )
    user = SimpleNamespace(id="U1", ontology_id=2, roles=["R"])
    try:
        await enforce(session, user, [], "read")
    except Forbidden:
        pass
    else:
        raise AssertionError("cross workspace user accepted")
    user.ontology_id = 1
    assert await policy_roles(session, user) == {"R"}
    assert await policy_roles(session, user) == {"R"} and session.scalars.await_count == 1
    session.info["ontology_id"] = 2
    user.ontology_id = 2
    assert await policy_roles(session, user) == {"R"} and session.scalars.await_count == 2
    session.scalar.return_value = "owner"
    user.id = "U2"
    assert await policy_roles(session, user) == {"R", "ontology:owner"}
    user.is_service_account = True
    assert await policy_roles(session, user) == {"R"}


def function_checks():
    function, rule, action, prop = (
        identity("function", "FN"),
        identity("rule", "RULE"),
        identity("action", "ACT"),
        identity("property", "OBJ", "secret"),
    )

    def graph():
        return {"nodes": [{"id": n} for n in (function, rule, action, prop)], "edges": [], "unresolved": []}

    objects = [
        SimpleNamespace(
            code="OBJ", api_name="Obj", display_name="对象", properties=[SimpleNamespace(api_name="secret")]
        )
    ]
    source = {"function": "FN", "version": "v1", "inputs": {"x": {"param": "p"}}}
    actions = [
        SimpleNamespace(code="ACT", params=[SimpleNamespace(default_text=json.dumps(source))], edits=[])
    ]
    rules = [SimpleNamespace(code="RULE", expr="rule_fn('FN','v1',Obj.secret)")]
    result = graph()
    add_function_edges(result, objects, rules, actions, {("FN", "v1")})
    assert {(e["source"], e["target"]) for e in result["edges"]} == {
        (function, action),
        (function, rule),
        (prop, rule),
    }
    for expr in (
        "(lambda Obj: rule_fn('FN','v1',Obj.secret))(x)",
        "[rule_fn('FN','v1',Obj.secret) for Obj in rows]",
        "rule_fn('FN','wrong',Obj.secret)",
    ):
        result = graph()
        add_function_edges(result, objects, [SimpleNamespace(code="RULE", expr=expr)], [], {("FN", "v1")})
        assert result["unresolved"] and not result["edges"]
    # Submission probes declare this request's purpose; persisted old runs never inherit a new header.
    source = ast.parse(
        (Path(__file__).resolve().parents[1] / "app/modules/integration/execution.py").read_text(
            encoding="utf-8"
        )
    )
    preflight = next(n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "preflight")
    assignment = next(n for n in preflight.body if isinstance(n, ast.Assign))
    for run, expected in (
        (SimpleNamespace(), "analysis"),
        (SimpleNamespace(data_purpose=None), None),
        (SimpleNamespace(data_purpose="stored"), "stored"),
    ):
        session = SimpleNamespace(info={"data_purpose": "analysis"})
        exec(compile(ast.Module([assignment], []), "<purpose>", "exec"), {"session": session, "run": run})
        assert session.info["data_purpose"] == expected


if __name__ == "__main__":
    policy_checks()
    function_checks()
    asyncio.run(query_checks())
    print("governance propagation, purpose, mask, SQL and workspace checks passed")
