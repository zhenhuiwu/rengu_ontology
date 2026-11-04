"""Offline lineage extraction, visibility and route boundaries; no database or HTTP."""

import ast
import asyncio
import json
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]


def check():
    source = ast.parse((ROOT / "app/modules/static/lineage.py").read_text(encoding="utf-8"))
    functions = [
        n
        for n in source.body
        if isinstance(n, ast.FunctionDef) and n.name in {"identity", "restrict_action_types", "build_lineage"}
    ]
    env = {
        "ast": ast,
        "json": json,
        "re": re,
        "sqlglot": sqlglot,
        "exp": exp,
        "IDENTIFIER": re.compile(r"^[A-Za-z_\u3400-\u9fff][\w$]*$"),
    }
    exec(compile(ast.Module(body=functions, type_ignores=[]), "lineage.py", "exec"), env)
    build, identity = env["build_lineage"], env["identity"]
    catalog = {
        "objects": [
            {
                "id": "Device",
                "api": "Device",
                "name": "设备",
                "rowAccess": "scoped",
                "properties": [{"api": "quantity", "name": "数量"}],
            }
        ],
        "metrics": [{"id": "quantity_sum", "name": "数量合计", "version": "v2"}],
        "rules": [{"id": "R1", "name": "巡检规则", "version": 1, "actionType": "A1", "rowAccess": "scoped"}],
        "actions": [
            {
                "id": "A1",
                "name": "登记巡检",
                "version": "v1",
                "rowAccess": "scoped",
                "params": [{"api": "threshold", "name": "阈值"}],
            }
        ],
    }
    definitions = {
        "objects": [
            {
                "id": "Device",
                "source": "DS-1",
                "table": "public.device",
                "properties": [
                    {"api": "quantity", "name": "数量", "source": "quantity", "transform": ""},
                    {"api": "private_key", "name": "private_key", "source": "private_key", "transform": ""},
                ],
            }
        ],
        "sourceVisible": True,
        "sources": [{"id": "DS-1", "name": "登记数据源", "password": "NEVER_EXPORT"}],
        "metrics": [
            {
                "id": "quantity_sum",
                "formula": "SUM(Device.quantity)",
                "def": [{"k": "基础对象", "v": "Device"}],
                "variants": [],
            }
        ],
        "rules": [
            {
                "id": "R1",
                "expr": "quantity_sum@v2(Device, day) > 1",
                "conds": [{"metric": "quantity_sum", "metricVer": 2}],
            }
        ],
        "actions": [
            {
                "id": "A1",
                "object": "Device",
                "conds": ["Device.quantity > 0"],
                "edits": [{"op": "修改", "obj": "Device", "field": '{"values":{"quantity":{"value":1}}}'}],
                "system": 7,
                "endpoint": "record",
                "mapping": {
                    "request": [
                        {"source": "object.quantity", "target": "amount"},
                        {"source": "params.threshold", "target": "limit"},
                        {"value": "NEVER_EXPORT", "target": "token"},
                    ]
                },
            }
        ],
        "systems": [
            {
                "id": 7,
                "code": "EXT-1",
                "name": "巡检系统",
                "secret": "NEVER_EXPORT",
                "endpoints": [
                    {
                        "id": "record",
                        "name": "登记接口",
                        "purpose": "action",
                        "path": "/NEVER_EXPORT",
                        "headers": {"token": "NEVER_EXPORT"},
                    }
                ],
            }
        ],
    }
    original = deepcopy(definitions)
    result = build(catalog, definitions)
    pairs = {(e["source"], e["target"], e["kind"]) for e in result["edges"]}
    prop, metric, rule, action = (
        identity("property", "Device", "quantity"),
        identity("metric", "quantity_sum"),
        identity("rule", "R1"),
        identity("action", "A1"),
    )
    assert (prop, metric, "reads") in pairs
    assert (metric, rule, "reads") in pairs
    assert (rule, action, "triggers") in pairs
    assert (action, identity("endpoint", "EXT-1", "record"), "dispatches") in pairs
    assert (identity("source", "DS-1", "public.device", "quantity"), prop, "maps") in pairs
    output = json.dumps(result, ensure_ascii=False)
    assert "NEVER_EXPORT" not in output and "private_key" not in output
    assert definitions == original
    variant_catalog, variant_definitions = deepcopy(catalog), deepcopy(definitions)
    variant_catalog["metrics"].append({"id": "quantity_alternative", "name": "另一口径", "version": "v1"})
    variant_definitions["metrics"][0]["variants"] = [{"code": "quantity_alternative", "default": False}]
    variant_definitions["metrics"].append(
        {
            "id": "quantity_alternative",
            "formula": "COUNT(Device)",
            "variants": [{"code": "quantity_sum", "default": True}],
        }
    )
    variant_graph = build(variant_catalog, variant_definitions)
    metric_nodes = {n["id"] for n in variant_graph["nodes"] if n["kind"] == "metric"}
    assert not any(
        e["source"] in metric_nodes and e["target"] in metric_nodes for e in variant_graph["edges"]
    )
    assert not any(e["evidence"] == "metrics.variants.code" for e in variant_graph["edges"])
    restrict = env["restrict_action_types"]
    assert restrict(catalog, None) == catalog
    assert restrict(catalog, ("A1",))["actions"] == catalog["actions"]
    for allowlist in ((), ("OTHER",)):
        restricted = restrict(catalog, allowlist)
        assert restricted["actions"] == []
        assert not any(
            n["kind"] in {"action", "parameter", "endpoint", "external"}
            for n in build(restricted, definitions)["nodes"]
        )
    assert catalog["actions"][0]["id"] == "A1"
    for section in ("objects", "metrics", "rules", "actions"):
        limited = deepcopy(catalog)
        limited[section] = []
        graph = build(limited, definitions)
        blocked = {
            identity(
                {"objects": "object", "metrics": "metric", "rules": "rule", "actions": "action"}[section],
                r["id"],
            )
            for r in catalog[section]
        }
        assert not any(n["id"] in blocked for n in graph["nodes"])
        assert not any(e["source"] in blocked or e["target"] in blocked for e in graph["edges"])
    denied = deepcopy(catalog)
    denied["objects"][0]["rowAccess"] = "none"
    assert not any(n["kind"] in {"object", "property", "source"} for n in build(denied, definitions)["nodes"])
    no_source = deepcopy(definitions)
    no_source["sourceVisible"] = False
    no_source["systems"] = []
    assert not any(
        n["kind"] in {"source", "external", "endpoint"} for n in build(catalog, no_source)["nodes"]
    )
    for formula in (
        "SUM(Device.private_key)",
        "SUM(Unknown.quantity)",
        "SUM(Device.quantity); SELECT 1",
        "(SELECT private_key FROM secret_table)",
        "SUM(Device.quantity) FROM secret_table",
        "(SELECT Device.quantity)",
        "SUM(Device.quantity) + (SELECT Device.quantity)",
        "Device.quantity AS renamed",
    ):
        unknown = deepcopy(definitions)
        unknown["metrics"][0]["formula"] = formula
        graph = build(catalog, unknown)
        assert any(i["node"] == metric and i["location"] == "metrics.formula" for i in graph["unresolved"])
        assert (prop, metric, "reads") not in {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}
        assert "secret_table" not in json.dumps(graph) and "private_key" not in json.dumps(graph)
    literal = deepcopy(definitions)
    literal["metrics"][0]["formula"] = "'Device.private_key'"
    assert (prop, metric, "reads") not in {
        (e["source"], e["target"], e["kind"]) for e in build(catalog, literal)["edges"]
    }
    for expression in (
        "(lambda Device: Device.quantity)(1)",
        "[Device.quantity for Device in []]",
        "sum(Device.quantity for Device in [])",
        "(Device := 1) and Device.quantity",
    ):
        local = deepcopy(definitions)
        local["rules"][0]["expr"] = expression
        local["rules"][0]["conds"] = []
        graph = build(catalog, local)
        assert any(i["node"] == rule and i["location"] == "rules.expr" for i in graph["unresolved"])
        assert not any(e["target"] == rule and e["evidence"] == "rules.expr" for e in graph["edges"])
    ambiguous = deepcopy(catalog)
    ambiguous["objects"][0]["properties"].append({"api": "other", "name": "数量"})
    definitions2 = deepcopy(definitions)
    definitions2["objects"][0]["properties"].append(
        {"api": "other", "name": "数量", "source": "other", "transform": ""}
    )
    definitions2["metrics"][0]["formula"] = 'SUM(Device."数量")'
    assert any(i["node"] == metric for i in build(ambiguous, definitions2)["unresolved"])

    loader = next(
        n for n in source.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "current_lineage"
    )
    for model in ("ObjectType", "Metric", "Rule", "ActionType", "Datasource", "ExtSystem"):
        assert any(
            isinstance(n, ast.Compare) and ast.unparse(n) == f"{model}.ontology_id == oid"
            for n in ast.walk(loader)
        )
    assert "user.ontology_id != oid" in ast.unparse(loader)
    assert "restrict_action_types(catalog, getattr(user, 'action_allowlist', None))" in ast.unparse(loader)
    route_tree = ast.parse((ROOT / "app/modules/static/lineage_router.py").read_text(encoding="utf-8"))
    route = next(n for n in route_tree.body if isinstance(n, ast.AsyncFunctionDef))
    assert "require_func('ontology-overview', 'view')" in ast.unparse(route.decorator_list[0])
    assert "require_api_scope('meta:read')" in ast.unparse(route.decorator_list[0])
    route.decorator_list = []
    for arg in route.args.args:
        arg.annotation = None
    calls = []

    async def current(*args):
        calls.append(args)
        return "current"

    class Error(Exception):
        def __init__(self, message, **kwargs):
            super().__init__(message)

    scope = {"AppError": Error, "current_lineage": current}
    exec(compile(ast.Module(body=[route], type_ignores=[]), "lineage_router.py", "exec"), scope)
    for ctx in (SimpleNamespace(ver="v1", preview_as=None), SimpleNamespace(ver=None, preview_as="other")):
        try:
            asyncio.run(scope["lineage"](None, None, ctx, None))
        except Error:
            pass
        else:
            raise AssertionError("history/preview must not borrow current definitions")
    assert not calls
    assert (
        asyncio.run(scope["lineage"](None, None, SimpleNamespace(ver=None, preview_as=None), None))
        == "current"
    )
    key_tree = ast.parse((ROOT / "app/modules/admin/api_keys.py").read_text(encoding="utf-8"))
    metadata = next(
        n.value
        for n in key_tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "METADATA_ROUTES" for t in n.targets)
    )
    assert ast.literal_eval(metadata)["/ontology/lineage"] == "ontology-overview"
    factory = next(
        n for n in key_tree.body if isinstance(n, ast.FunctionDef) and n.name == "require_api_scope"
    )
    dependency = next(n for n in factory.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_dep")
    dependency.returns = None
    for arg in dependency.args.args:
        arg.annotation = None
    security = {
        "scope": "meta:read",
        "key_only": False,
        "action_type": None,
        "event_type": None,
        "function_type": None,
        "Forbidden": Error,
        "Unauthorized": Error,
        "enforce_api_allowlist": lambda *args, **kwargs: None,
        "_path_value": lambda *args: None,
    }
    exec(compile(ast.Module(body=[dependency], type_ignores=[]), "api_keys.py", "exec"), security)
    for scopes in ((), ("objects:read",)):
        try:
            asyncio.run(
                security["_dep"](
                    SimpleNamespace(state=SimpleNamespace(api_key=SimpleNamespace(scopes=scopes))), "user"
                )
            )
        except Error:
            pass
        else:
            raise AssertionError("meta:read must be required")
    assert (
        asyncio.run(
            security["_dep"](
                SimpleNamespace(state=SimpleNamespace(api_key=SimpleNamespace(scopes=("meta:read",)))), "user"
            )
        )
        == "user"
    )
    assert (
        asyncio.run(security["_dep"](SimpleNamespace(state=SimpleNamespace(api_key=None)), "human"))
        == "human"
    )


def check_source_database():
    import sys

    sys.path.insert(0, str(ROOT))
    from app.modules.governance.source_write import mapped_properties
    from app.modules.static.lineage import build_lineage, identity
    from scripts.check_governance_source_write import fixture

    system, grant, source, obj, action = fixture()
    writes, keys = mapped_properties(1, system, grant, source, obj, action)
    catalog = {
        "objects": [
            {
                "id": obj.code,
                "api": obj.code,
                "name": "Device",
                "rowAccess": "scoped",
                "properties": [{"api": p.api_name, "name": p.api_name} for p in obj.properties],
            }
        ],
        "actions": [{"id": "A", "name": "Write score", "version": "v2", "rowAccess": "scoped"}],
    }
    definitions = {
        "objects": [
            {
                "id": obj.code,
                "source": source.code,
                "table": obj.source_table,
                "properties": [
                    {"api": p.api_name, "name": p.api_name, "source": p.source_column} for p in obj.properties
                ],
            }
        ],
        "actions": [
            {
                "id": "A",
                "version": "v2",
                "system": system.id,
                "sourceWrite": {"writes": sorted(writes), "keys": sorted(keys)},
            }
        ],
        "systems": [{"id": system.id, "code": system.code, "name": "Writer", "provider": "database"}],
        "sources": [{"id": source.code, "name": "Read source"}],
        "sourceVisible": True,
    }
    target = identity("action", "A")
    graph = build_lineage(catalog, definitions)
    pairs = {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}
    assert all((prop, target, "request") in pairs for prop in writes)  # All physical-column aliases.
    assert all((prop, target, "reads") in pairs for prop in keys)
    assert (target, identity("external", system.code), "dispatches") in pairs
    assert not any(i["location"] == "actions.httpEndpointId" for i in graph["unresolved"])
    for mutation in ("missing", "version", "source_hidden", "property_hidden", "external_hidden"):
        changed, visible = deepcopy(definitions), deepcopy(catalog)
        if mutation == "missing":
            changed["actions"][0].pop("sourceWrite")
        elif mutation == "version":
            changed["actions"][0]["version"] = "v3"
        elif mutation == "source_hidden":
            changed["sourceVisible"] = False
        elif mutation == "property_hidden":
            visible["objects"][0]["properties"] = []
        else:
            changed["systems"] = []
        result = build_lineage(visible, changed)
        assert not any(e["evidence"] == "source_write_binding.actions.fields" for e in result["edges"])
        if mutation != "external_hidden":
            assert any(i["location"] == "source_write_binding.actions.fields" for i in result["unresolved"])


if __name__ == "__main__":
    check()
    check_source_database()
    print(
        "Lineage checks passed: explicit pipeline, AST boundaries, capability/property visibility, "
        "no secrets/history mixing."
    )
