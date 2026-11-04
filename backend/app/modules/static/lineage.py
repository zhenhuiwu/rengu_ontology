"""Definition lineage only; never execute expressions or infer runtime data flow."""

import ast
import json
import re

import sqlglot
from sqlglot import exp
from app.core.errors import AppError

IDENTIFIER = re.compile(r"^[A-Za-z_\u3400-\u9fff][\w$]*$")


def identity(kind, *parts):
    return json.dumps([kind, *parts], ensure_ascii=False, separators=(",", ":"))


def restrict_action_types(catalog, allowlist):
    if allowlist is None:
        return catalog
    return {**catalog, "actions": [a for a in catalog.get("actions", []) if a["id"] in allowlist]}


def build_lineage(catalog, definitions):
    """All input definitions are intersected with the caller's capability/property catalog."""
    nodes, edges, issues = {}, {}, {}
    allowed = {
        section: {row["id"]: row for row in catalog.get(section, []) if row.get("rowAccess") != "none"}
        for section in ("objects", "metrics", "rules", "actions")
    }

    def node(kind, code, name, *keys, version=None):
        key = identity(kind, *(keys or (code,)))
        nodes[key] = {"id": key, "kind": kind, "code": code, "name": name}
        if version is not None:
            nodes[key]["version"] = str(version)
        return key

    def edge(source, target, kind, evidence):
        if source in nodes and target in nodes and source != target:
            key = identity("edge", source, target, kind, evidence)
            edges[key] = {"id": key, "source": source, "target": target, "kind": kind, "evidence": evidence}

    def unresolved(target, location):
        if target in nodes:
            issues[target, location] = {
                "node": target,
                "location": location,
                "reason": "引用未能唯一解析为可见的显式定义，未推测依赖",
            }

    aliases, properties = {}, {}
    for obj in definitions.get("objects", []):
        public = allowed["objects"].get(obj["id"])
        if public is None:
            continue
        key = node("object", obj["id"], public["name"])
        for alias in {obj["id"], public["api"], public["name"]}:
            aliases.setdefault(alias, set()).add(obj["id"])
        visible = {p["api"] for p in public.get("properties", [])}
        private = {p.get("source") for p in obj["properties"] if p["api"] not in visible}
        for prop in obj["properties"]:
            if prop["api"] not in visible:
                continue
            pk = node("property", prop["api"], prop["name"], obj["id"], prop["api"])
            edge(key, pk, "contains", "objects.properties")
            for name in {prop["api"], prop["name"]}:
                properties.setdefault((obj["id"], name), set()).add(pk)
            sources = [s for s in definitions.get("sources", []) if obj.get("source") in (s["id"], s["name"])]
            table, column = obj.get("table") or "", prop.get("source") or ""
            if not definitions.get("sourceVisible"):
                continue
            if (
                len(sources) != 1
                or prop.get("transform")
                or column in private
                or not IDENTIFIER.fullmatch(column)
                or not table
                or any(not IDENTIFIER.fullmatch(part) for part in table.split("."))
            ):
                unresolved(pk, "properties.source")
                continue
            source = sources[0]
            sk = node(
                "source",
                f"{table}.{column}",
                f"{source['name']} · {table}.{column}",
                source["id"],
                table,
                column,
            )
            if obj.get("sourceUnresolved"):
                unresolved(sk, "source.view_definition")
            edge(sk, pk, "maps", "properties.source_column")

    def object_key(name):
        if not isinstance(name, str):
            return None
        matches = aliases.get(name, set())
        return next(iter(matches)) if len(matches) == 1 else None

    def property_key(owner, name):
        matches = properties.get((object_key(owner), name), set())
        return next(iter(matches)) if len(matches) == 1 else None

    metric_keys = {}
    for code, metric in allowed["metrics"].items():
        metric_keys[code] = node("metric", code, metric["name"], version=metric.get("version"))

    def sql_references(text, target, location, owner=None):
        if not text:
            return
        try:
            if not isinstance(text, str) or len(text) > 4000:
                raise ValueError()
            trees = sqlglot.parse("SELECT " + text, read="postgres")
            if len(trees) != 1 or not isinstance(trees[0], exp.Select):
                raise ValueError()
            tree = trees[0]
            if (
                sum(1 for _ in tree.walk()) > 256
                or len(tree.expressions) != 1
                or any(
                    value is not None
                    for name, value in tree.args.items()
                    if name not in ("expressions", "where")
                )
                or any(
                    isinstance(item, (exp.Table, exp.Alias, exp.CTE, exp.Subquery))
                    or (isinstance(item, exp.Select) and item is not tree)
                    for item in tree.walk()
                )
            ):
                raise ValueError()
            for col in tree.find_all(exp.Column):
                if col.db or col.catalog:
                    raise ValueError()
                source = property_key(col.table or owner, col.name)
                if source:
                    edge(source, target, "reads", location)
                elif not col.table and object_key(col.name):
                    edge(identity("object", object_key(col.name)), target, "aggregates", location)
                else:
                    unresolved(target, location)
            if list(tree.find_all(exp.Anonymous)):
                unresolved(target, location)
        except (ValueError, sqlglot.errors.ParseError, sqlglot.errors.TokenError, RecursionError):
            unresolved(target, location)

    for metric in definitions.get("metrics", []):
        target = metric_keys.get(metric["id"])
        if target is None:
            continue
        metadata = {row.get("k"): row.get("v") for row in metric.get("def", []) if isinstance(row, dict)}
        owner = metadata.get("基础对象")
        if owner and object_key(owner):
            edge(identity("object", object_key(owner)), target, "aggregates", "metrics.baseObject")
        if not metric.get("formula"):
            unresolved(target, "metrics.formula")
        try:
            from app.modules.static.metric_program import is_program, references
            from app.modules.static.metric_trial import parse_formula
            formula = parse_formula(metric.get("formula"))
            if is_program(formula):
                for reference, version in references(formula):
                    candidates = [m for m in allowed["metrics"].values()
                                  if reference in (m["id"], m.get("api"))]
                    if len(candidates) == 1 and candidates[0].get("version") == version:
                        edge(metric_keys[candidates[0]["id"]], target, "reads", "metrics.formula.metricVersion")
                    else:
                        unresolved(target, "metrics.formula.metricVersion")
            else:
                sql_references(metric.get("formula"), target, "metrics.formula", owner)
        except AppError:
            unresolved(target, "metrics.formula")
        sql_references(metadata.get("默认过滤"), target, "metrics.defaultFilter", owner)
        if field := metadata.get("时间字段"):
            sql_references(field, target, "metrics.timeField", owner)

    def expression(text, target, location):
        if not text:
            return
        try:
            if not isinstance(text, str) or len(text) > 4000:
                raise ValueError()
            tree = ast.parse(text, mode="eval")
            if sum(1 for _ in ast.walk(tree)) > 256 or any(
                isinstance(
                    item,
                    (
                        ast.Lambda,
                        ast.comprehension,
                        ast.NamedExpr,
                        ast.ListComp,
                        ast.SetComp,
                        ast.DictComp,
                        ast.GeneratorExp,
                    ),
                )
                for item in ast.walk(tree)
            ):
                raise ValueError()
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            known = set()
            for ref in ast.walk(tree):
                if ref in known:
                    continue
                if isinstance(ref, ast.Attribute):
                    source = property_key(ref.value.id, ref.attr) if isinstance(ref.value, ast.Name) else None
                    if source:
                        edge(source, target, "reads", location)
                    else:
                        unresolved(target, location)
                elif isinstance(ref, ast.BinOp) and isinstance(ref.op, ast.MatMult):
                    if (
                        isinstance(ref.left, ast.Name)
                        and ref.left.id in metric_keys
                        and isinstance(ref.right, ast.Call)
                        and isinstance(ref.right.func, ast.Name)
                        and len(ref.right.args) == 2
                        and not ref.right.keywords
                        and all(isinstance(arg, ast.Name) for arg in ref.right.args)
                        and object_key(ref.right.args[0].id) is not None
                        and ref.right.args[1].id in ("day", "week")
                        and ref.right.func.id
                        == "v" + str(allowed["metrics"][ref.left.id].get("version", "")).lstrip("v")
                    ):
                        edge(metric_keys[ref.left.id], target, "reads", location)
                        known.update(ast.walk(ref))
                    else:
                        unresolved(target, location)
                elif isinstance(ref, ast.Call) or (
                    isinstance(ref, ast.Name) and not isinstance(parents.get(ref), ast.Attribute)
                ):
                    unresolved(target, location)
        except (SyntaxError, ValueError, RecursionError):
            unresolved(target, location)

    action_keys = {}
    for code, action in allowed["actions"].items():
        action_keys[code] = node("action", code, action["name"], version=action.get("version"))
    for rule in definitions.get("rules", []):
        public = allowed["rules"].get(rule["id"])
        if public is None:
            continue
        target = node("rule", rule["id"], public["name"], version=public.get("version"))
        expression(rule.get("expr"), target, "rules.expr")
        for cond in rule.get("conds") or []:
            if not isinstance(cond, dict):
                unresolved(target, "rules.conds")
                continue
            code = cond.get("metric")
            if code in metric_keys:
                version = cond.get("metricVer")
                if version is None or str(version).lstrip("v") == str(
                    allowed["metrics"][code].get("version")
                ).lstrip("v"):
                    edge(metric_keys[code], target, "reads", "rules.conds.metric")
                else:
                    unresolved(target, "rules.conds.metricVer")
            elif code:
                unresolved(target, "rules.conds.metric")
            if field := cond.get("field"):
                source = property_key(public.get("object"), field)
                if source:
                    edge(source, target, "reads", "rules.conds.field")
                else:
                    unresolved(target, "rules.conds.field")
        action = public.get("actionType")
        if action in action_keys:
            edge(target, action_keys[action], "triggers", "rules.actionTypeId")
        stage = rule.get("autoStage") or {}
        if isinstance(stage, dict) and stage.get("enabled"):
            code = stage.get("actionTypeId")
            if code in action_keys:
                edge(target, action_keys[code], "triggers", "rules.autoStage.actionTypeId")
            else:
                unresolved(target, "rules.autoStage.actionTypeId")
            if stage.get("mapping"):
                unresolved(target, "rules.autoStage.mapping")

    for action in definitions.get("actions", []):
        target = action_keys.get(action["id"])
        if target is None:
            continue
        for cond in action.get("conds", []):
            expression(cond, target, "actions.conds.expr")
        for edit in action.get("edits", []):
            try:
                spec = json.loads(edit["field"])
                assignments = spec.get("values", spec)
                if not isinstance(assignments, dict) or edit.get("op") != "修改":
                    raise ValueError()
                for field in assignments:
                    destination = property_key(edit.get("obj"), field)
                    if destination:
                        edge(target, destination, "writes", "actions.writeback.edits")
                    else:
                        unresolved(target, "actions.writeback.edits")
                if any(isinstance(value, dict) and set(value) != {"value"} for value in assignments.values()):
                    unresolved(target, "actions.writeback.sources")
            except (TypeError, ValueError, AttributeError):
                unresolved(target, "actions.writeback.edits")
        system = next((s for s in definitions.get("systems", []) if s["id"] == action.get("system")), None)
        if system is None:
            continue
        external = node("external", system["code"], system["name"])
        if system.get('provider') == 'database':
            edge(target, external, 'dispatches', 'actions.sysId')
            mapping = action.get('sourceWrite')
            if (not definitions.get('sourceVisible') or not isinstance(mapping, dict)
                    or not mapping.get('writes') or not mapping.get('keys')
                    or action.get('version') != allowed['actions'][action['id']].get('version')):
                unresolved(target, 'source_write_binding.actions.fields')
                continue
            for kind, references, evidence in (
                ('request', mapping['writes'], 'source_write_binding.actions.fields'),
                ('reads', mapping['keys'], 'source_write_binding.key_column'),
            ):
                for source in references:
                    if source in nodes:
                        edge(source, target, kind, evidence)
                    else:
                        unresolved(target, evidence)
            continue
        endpoint = action.get("endpoint")
        if not endpoint:
            edge(target, external, "dispatches", "actions.sysId")
            unresolved(target, "actions.httpEndpointId")
            continue
        declared = [
            e
            for e in system.get("endpoints") or []
            if isinstance(e, dict) and e.get("id") == endpoint and e.get("purpose") == "action"
        ]
        if len(declared) != 1:
            unresolved(target, "actions.httpEndpointId")
            continue
        ek = node("endpoint", endpoint, declared[0].get("name") or endpoint, system["code"], endpoint)
        edge(target, ek, "dispatches", "actions.httpEndpointId")
        edge(ek, external, "belongs", "external.http_endpoints")
        params = {p["api"]: p for p in allowed["actions"][action["id"]].get("params", [])}
        mapping = action.get("mapping") or {}
        for row in mapping.get("request", []) if isinstance(mapping, dict) else []:
            source = row.get("source") if isinstance(row, dict) else None
            if not isinstance(source, str):
                continue
            if source.startswith("object."):
                pk = property_key(action.get("object"), source[7:])
                if pk:
                    edge(pk, target, "reads", "actions.httpMapping.request.object")
                    edge(pk, ek, "request", "actions.httpMapping.request.object")
                else:
                    unresolved(target, "actions.httpMapping.request.object")
            elif source.startswith("params."):
                param = params.get(source[7:])
                if param:
                    pk = node("parameter", param["api"], param["name"], action["id"], param["api"])
                    edge(target, pk, "contains", "actions.params")
                    edge(pk, ek, "request", "actions.httpMapping.request.params")
                else:
                    unresolved(target, "actions.httpMapping.request.params")
    return {
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "unresolved": list(issues.values()),
        "scope": "current",
        "notes": ["仅展示当前身份可见定义的显式依赖，不代表历史数据流或分类分级传播。"],
    }


async def current_lineage(session, user, request=None, *, _catalog=None):
    from sqlalchemy import select
    from sqlalchemy.orm import raiseload, selectinload

    from app.common.ontology import require_ontology_id
    from app.core.errors import AppError
    from app.modules.action.models import ActionType
    from app.modules.data.models import Datasource, ExternalReadBinding
    from app.modules.dynamic.rules_models import Rule
    from app.modules.integration.models import ExtSystem
    from app.modules.static.capabilities import capabilities
    from app.modules.static.models import Metric, ObjectType

    oid = require_ontology_id(session)
    if user.ontology_id != oid:
        raise AppError("血缘身份与本体不一致", code="ONTOLOGY_SCOPE_CONFLICT", status_code=403)
    catalog = _catalog if _catalog is not None else await capabilities(session, user, request, readable_only=True)
    catalog = restrict_action_types(catalog, getattr(user, "action_allowlist", None))
    ids = {
        key: [r["id"] for r in catalog.get(key, []) if r.get("rowAccess") != "none"]
        for key in ("objects", "metrics", "rules", "actions")
    }
    definitions = {"objects": [], "metrics": [], "rules": [], "actions": [], "sources": [], "systems": []}
    view_objects = set(await session.scalars(select(ExternalReadBinding.object_code).where(
        ExternalReadBinding.ontology_id == oid, ExternalReadBinding.object_code.in_(ids["objects"]),
        ExternalReadBinding.declared_unique_key.is_not(None),
        ExternalReadBinding.declared_unique_key != "")))
    for obj in await session.scalars(
        select(ObjectType).where(ObjectType.ontology_id == oid, ObjectType.code.in_(ids["objects"]))
    ):
        definitions["objects"].append(
            {
                "id": obj.code,
                "source": obj.datasource_name,
                "table": obj.source_table,
                "sourceUnresolved": obj.code in view_objects,
                "properties": [
                    {
                        "api": p.api_name,
                        "name": p.display_name,
                        "source": p.source_column,
                        "transform": p.transform,
                    }
                    for p in obj.properties
                ],
            }
        )
    for metric in await session.scalars(
        select(Metric).where(Metric.ontology_id == oid, Metric.code.in_(ids["metrics"]))
    ):
        definitions["metrics"].append(
            {
                "id": metric.code,
                "formula": metric.formula,
                "def": metric.def_rows,
            }
        )
    for rule in await session.scalars(
        select(Rule).where(Rule.ontology_id == oid, Rule.code.in_(ids["rules"]))
    ):
        definitions["rules"].append(
            {"id": rule.code, "expr": rule.expr, "conds": rule.conds, "autoStage": rule.auto_stage}
        )
    statement = (
        select(ActionType)
        .where(ActionType.ontology_id == oid, ActionType.code.in_(ids["actions"]))
        .options(raiseload("*"), selectinload(ActionType.conds),
                 selectinload(ActionType.edits), selectinload(ActionType.params))
    )
    for action in await session.scalars(statement):
        definitions["actions"].append(
            {
                "id": action.code,
                "version": action.version,
                "params": [{"api": p.api} for p in action.params],
                "object": action.target_kind,
                "conds": [c.expr_text for c in action.conds],
                "edits": [{"op": e.op.value, "obj": e.obj, "field": e.field} for e in action.edits],
                "system": action.sys_id,
                "endpoint": action.http_endpoint_id,
                "mapping": action.http_mapping,
            }
        )
    definitions["sourceVisible"] = user.func.get("connections") in ("view", "manage")
    if definitions["sourceVisible"]:
        definitions["sources"] = [
            {"id": code, "name": name}
            for code, name in await session.execute(
                select(Datasource.code, Datasource.name).where(Datasource.ontology_id == oid)
            )
        ]
    if user.func.get("external-systems") in ("view", "manage"):
        definitions["systems"] = [
            {"id": row.id, "code": row.code, "name": row.name, "endpoints": row.http_endpoints,
             "provider": 'database' if row.endpoint.startswith('database://') else 'http'}
            for row in await session.execute(
                select(ExtSystem.id, ExtSystem.code, ExtSystem.name,
                       ExtSystem.http_endpoints, ExtSystem.endpoint).where(
                    ExtSystem.ontology_id == oid
                )
            )
        ]
        if definitions['sourceVisible']:
            from app.modules.governance.source_write import bindings, mapped_properties
            system_ids = {action['system'] for action in definitions['actions']
                          if action['system'] is not None}
            rows = await bindings(session, system_ids=system_ids) if system_ids else []
            by_system = {row[0].id: row for row in rows}
            for action in definitions['actions']:
                bound = by_system.get(action['system'])
                if bound is None or not bound[0].endpoint.startswith('database://'):
                    continue
                try:
                    writes, keys = mapped_properties(oid, *bound, {
                        'id': action['id'], 'version': action['version'], 'params': action['params'],
                        'sysId': bound[0].code, 'targetKind': action['object']})
                except AppError:
                    continue  # The pure graph reports the unresolved mapping without exposing private fields.
                action['sourceWrite'] = {'writes': sorted(writes), 'keys': sorted(keys)}
    return build_lineage(catalog, definitions)
