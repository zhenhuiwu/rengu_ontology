"""Pure source-write propagation, view ancestry and preflight guards; no DB, HTTP or pytest."""

import ast
import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

import app.db.models  # noqa: F401
from app.core.errors import AppError, Forbidden
from app.modules.data.models import DsType
from app.modules.governance import source_write
from app.modules.governance.policy import effective_policies
from app.modules.integration.models import ExtSystemStatus
from app.modules.integration.source_database import source_signature
from app.modules.static.lineage import build_lineage, identity
from app.modules.static.models import ObjectStatus


def fixture():
    def prop(api, column, primary=False):
        return SimpleNamespace(
            api_name=api,
            source_column=column,
            is_pk=primary,
            is_sensitive=False,
            visibility="visible",
            transform=None,
        )

    system = SimpleNamespace(
        id=7, ontology_id=1, code="EXT", endpoint="database://DS", status=ExtSystemStatus.ok, idempotent=True
    )
    source = SimpleNamespace(
        ontology_id=1,
        code="DS",
        name="Source",
        type=DsType.mysql,
        host="host",
        port=3306,
        database="source",
        ssl=False,
    )
    obj = SimpleNamespace(
        ontology_id=1,
        code="OBJ",
        status=ObjectStatus.published,
        datasource_name="DS",
        source_table="public.fixture",
        pk_property="Id",
        properties=[prop("Id", "id", True), prop("Score", "score"), prop("ScoreAlias", "score")],
    )
    grant = SimpleNamespace(
        ontology_id=1,
        sys_id=7,
        enabled=True,
        datasource_code="DS",
        source_signature=source_signature(source),
        object_code="OBJ",
        schema_name="public",
        table_name="fixture",
        key_column="id",
        write_columns=["score"],
        actions={"A": {"version": "v2", "fields": {"score": "value"}}},
    )
    action = {"id": "A", "version": "v2", "sysId": "EXT", "targetKind": "OBJ", "params": [{"api": "value"}]}
    return system, grant, source, obj, action


def mapping_checks():
    system, grant, source, obj, action = fixture()
    writes, keys = source_write.mapped_properties(1, system, grant, source, obj, action)
    assert writes == {identity("property", "OBJ", "Score"), identity("property", "OBJ", "ScoreAlias")}
    assert keys == {identity("property", "OBJ", "Id")}
    mutations = [
        lambda s, g, d, o, a: setattr(g, "ontology_id", 2),
        lambda s, g, d, o, a: setattr(d, "ontology_id", 2),
        lambda s, g, d, o, a: setattr(o, "ontology_id", 2),
        lambda s, g, d, o, a: setattr(g, "source_signature", "changed"),
        lambda s, g, d, o, a: setattr(g, "enabled", False),
        lambda s, g, d, o, a: setattr(o, "source_table", "public.other"),
        lambda s, g, d, o, a: setattr(o, "datasource_name", "other"),
        lambda s, g, d, o, a: a.update(version="v3"),
        lambda s, g, d, o, a: a.update(targetKind="OTHER"),
        lambda s, g, d, o, a: setattr(g, "write_columns", []),
        lambda s, g, d, o, a: g.actions["A"]["fields"].update(missing="value"),
        lambda s, g, d, o, a: g.actions["A"]["fields"].update(score="undeclared"),
        lambda s, g, d, o, a: setattr(o.properties[2], "is_sensitive", True),
        lambda s, g, d, o, a: setattr(g, "actions", []),
    ]
    for mutation in mutations:
        values = fixture()
        mutation(*values)
        try:
            source_write.mapped_properties(1, *values)
        except AppError as exc:
            assert exc.code == "GOVERNANCE_SOURCE_WRITE_UNRESOLVED"
        else:
            raise AssertionError("unsafe source write mapping accepted")
    target, external = identity("action", "A"), identity("external", "EXT")
    graph = {
        "nodes": [{"id": n} for n in writes | keys | {target, external}],
        "edges": [],
        "unresolved": [{"node": target, "location": "actions.httpEndpointId"}],
    }
    raw_action = SimpleNamespace(
        code="A", version="v2", target_kind="OBJ", sys_id=7, params=[SimpleNamespace(api="value")]
    )
    source_write.add_edges(graph, [raw_action], [(system, grant, source, obj)], 1)
    assert not graph["unresolved"]
    assert any(
        e["source"] == identity("property", "OBJ", "Score") and e["target"] == target for e in graph["edges"]
    )
    definition = {
        "resources": [
            {"resource": identity("property", "OBJ", "Score"), "classes": ["private"], "purposes": ["verify"]}
        ]
    }
    states = effective_policies(definition, graph)
    assert states[target]["classes"] == states[external]["classes"] == ["private"]
    broken = deepcopy(graph)
    broken["edges"] = []
    grant.actions["A"]["version"] = "old"
    source_write.add_edges(broken, [raw_action], [(system, grant, source, obj)], 1)
    assert broken["unresolved"] and not broken["edges"]
    assert "host" not in json.dumps(graph) and "password" not in json.dumps(graph)


def catalog_and_view_checks():
    tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "app/modules/governance/policy.py").read_text(encoding="utf-8")
    )
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "policy_graph")
    assignment = next(
        n
        for n in function.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "catalog"
    )
    env = {
        "objects": [],
        "metrics": [
            SimpleNamespace(code="M", api_name="metric_api", display_name="Metric", version="v1"),
            SimpleNamespace(code="C", api_name="composite", display_name="Composite", version="v1"),
        ],
        "actions": [],
        "rules": [],
        "action_codes": {},
    }
    exec(compile(ast.Module([assignment], []), "<catalog>", "exec"), env)
    catalog = env["catalog"]
    graph = build_lineage(
        catalog,
        {
            "metrics": [
                {"id": "M", "formula": "COUNT(*)"},
                {"id": "C", "formula": "METRIC('metric_api','v1') * 2"},
            ]
        },
    )
    assert any(
        e["source"] == identity("metric", "M") and e["target"] == identity("metric", "C")
        for e in graph["edges"]
    )
    assert not any(r["node"] == identity("metric", "C") for r in graph["unresolved"])
    catalog = {
        "objects": [
            {"id": "OBJ", "api": "Obj", "name": "Object", "properties": [{"api": "Score", "name": "Score"}]}
        ]
    }
    definitions = {
        "sourceVisible": True,
        "sources": [{"id": "DS", "name": "Source"}],
        "objects": [
            {
                "id": "OBJ",
                "source": "DS",
                "table": "public.view",
                "sourceUnresolved": True,
                "properties": [{"api": "Score", "name": "Score", "source": "score"}],
            }
        ],
    }
    graph = build_lineage(catalog, definitions)
    source_id = identity("source", "DS", "public.view", "score")
    assert any(
        r["node"] == source_id and r["location"] == "source.view_definition" for r in graph["unresolved"]
    )
    definition = {"resources": [{"resource": identity("property", "OBJ", "Score"), "purposes": ["verify"]}]}
    assert effective_policies(definition, graph)[identity("property", "OBJ", "Score")]["unresolved"]
    definition["resources"].append(
        {
            "resource": source_id,
            "purposes": ["verify"],
            "dependencies_complete": True,
            "reason": "Explicit view dependency review",
        }
    )
    assert not effective_policies(definition, graph)[identity("property", "OBJ", "Score")]["unresolved"]


async def preflight_checks():
    system, grant, source, obj, action = fixture()
    session = SimpleNamespace(info={"ontology_id": 1})
    run = SimpleNamespace(obj_kind="OBJ", type_version="v2")
    recorder = AsyncMock(return_value={})
    with (
        patch.object(source_write, "bindings", AsyncMock(return_value=[(system, grant, source, obj)])),
        patch.object(source_write, "enforce", recorder),
        patch("app.modules.integration.source_database.prepare", AsyncMock()) as prepare,
    ):
        await source_write.preflight(session, SimpleNamespace(), run, action)
        assert recorder.await_count == 2 and prepare.await_count == 0
        assert recorder.await_args_list[0].args[3] == "execute"
        assert recorder.await_args_list[1].args[3] == "external"
        assert all(call.kwargs["unmasked"] is True for call in recorder.await_args_list)
        assert identity("property", "OBJ", "Score") in recorder.await_args_list[1].args[2]
        recorder.reset_mock()
        recorder.side_effect = Forbidden("denied")
        try:
            await source_write.preflight(session, SimpleNamespace(), run, action)
        except Forbidden:
            pass
        else:
            raise AssertionError("source write continued after denied property")
        assert recorder.await_count == 1 and prepare.await_count == 0

    async def execute(statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "password_enc" not in sql and "username" not in sql
        assert all(
            f"{name}.ontology_id" in sql
            for name in ("ext_system", "source_write_binding", "datasource", "object_type")
        )
        return []

    session.execute = execute
    assert await source_write.bindings(session, system_code="EXT") == []


if __name__ == "__main__":
    mapping_checks()
    catalog_and_view_checks()
    asyncio.run(preflight_checks())
    print("source-write field propagation, metric API, view ancestry and guards passed")
