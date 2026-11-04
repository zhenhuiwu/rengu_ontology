"""Only fixed function bindings and explicit call arguments establish dependencies."""

import ast
import json

from app.modules.static.lineage import identity


def add_function_edges(graph, objects, rules, actions, versions):
    nodes = {n["id"] for n in graph["nodes"]}
    aliases = {}
    for obj in objects:
        for alias in (obj.code, obj.api_name, obj.display_name):
            aliases.setdefault(alias, set()).add(obj.code)
    props = {(obj.code, prop.api_name) for obj in objects for prop in obj.properties}

    def edge(source, target, evidence):
        if source in nodes and target in nodes and source != target:
            row = {
                "id": identity("edge", source, target, "reads", evidence),
                "source": source,
                "target": target,
                "kind": "reads",
                "evidence": evidence,
            }
            if not any(e["id"] == row["id"] for e in graph["edges"]):
                graph["edges"].append(row)

    def unknown(target, location):
        graph["unresolved"].append(
            {"node": target, "location": location, "reason": "固定函数或输入依赖未能确认"}
        )

    def binding(source, target, depth=0):
        if depth > 4 or not isinstance(source, dict):
            unknown(target, "function.inputs")
            return
        if set(source) in ({"value"}, {"param"}):
            return
        if set(source) != {"function", "version", "inputs"} or not isinstance(source["inputs"], dict):
            unknown(target, "function.inputs")
            return
        code, version = source["function"], source["version"]
        if not isinstance(code, str) or not isinstance(version, str) or (code, version) not in versions:
            unknown(target, "function.version")
            return
        edge(identity("function", code), target, "action.fixed_function")
        for value in source["inputs"].values():
            binding(value, target, depth + 1)

    for action in actions:
        target = identity("action", action.code)
        for param in action.params:
            raw = param.default_text
            if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
                continue
            try:
                value = json.loads(raw)
                if isinstance(value, dict) and "function" in value:
                    binding(value, target)
            except (ValueError, TypeError):
                unknown(target, "action.param.function")
        for edit in action.edits:
            try:
                value = json.loads(edit.field)
                if isinstance(value, dict):
                    for source in (value.get("values") or value).values():
                        if isinstance(source, dict) and "function" in source:
                            binding(source, target)
            except (ValueError, TypeError, AttributeError):
                continue  # Existing lineage parser retains this edit as unresolved.
    for rule in rules:
        target = identity("rule", rule.code)
        try:
            tree = ast.parse(rule.expr or "", mode="eval")
            if any(isinstance(n, (ast.Lambda, ast.comprehension, ast.NamedExpr)) for n in ast.walk(tree)):
                raise ValueError()
            for call in (
                n
                for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "rule_fn"
            ):
                if (
                    call.keywords
                    or len(call.args) < 2
                    or not all(
                        isinstance(n, ast.Constant) and isinstance(n.value, str) for n in call.args[:2]
                    )
                ):
                    raise ValueError()
                code, version = [n.value for n in call.args[:2]]
                if (code, version) not in versions:
                    raise ValueError()
                refs = []
                for arg in call.args[2:]:
                    if isinstance(arg, ast.Constant):
                        continue
                    if not isinstance(arg, ast.Attribute) or not isinstance(arg.value, ast.Name):
                        raise ValueError()
                    owners = aliases.get(arg.value.id, set())
                    if len(owners) != 1 or (next(iter(owners)), arg.attr) not in props:
                        raise ValueError()
                    refs.append(identity("property", next(iter(owners)), arg.attr))
                edge(identity("function", code), target, "rule.fixed_function")
                for source in refs:
                    edge(source, target, "rule.function.inputs")
        except (SyntaxError, ValueError, RecursionError):
            if "rule_fn" in (rule.expr or ""):
                unknown(target, "rule.function")
