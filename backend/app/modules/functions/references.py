"""Current fixed-version references, merged with the conservative legacy ledger."""

import ast
import json

from sqlalchemy import literal, select, union_all

from app.common.ontology import require_ontology_id
from app.core.errors import AppError
from app.modules.action.models import ActionParam, ActionType, OntologyEditRule, TypeStatus
from app.modules.dynamic.rule_runtime import parse_expression
from app.modules.dynamic.rules_models import Rule
from app.modules.functions.models import FnUse, Function


def referenced_codes(text: str, mode: str) -> set[str]:
    if mode == "rule":
        try:
            tree = parse_expression(text)
        except AppError:
            return set()
        return {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "rule_fn" and len(node.args) >= 2
            and all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args[:2])
            and node.args[1].value and node.args[1].value != "current"
        }
    try:
        value = json.loads(text)
    except (ValueError, TypeError, RecursionError):
        return set()
    if not isinstance(value, dict):
        return set()
    values = value.get("values", value)
    sources = [value] if mode == "param" else list(values.values()) if isinstance(values, dict) else []
    found = set()
    while sources:
        source = sources.pop()
        if not isinstance(source, dict) or set(source) != {"function", "version", "inputs"}:
            continue
        if not isinstance(source["function"], str) or not isinstance(source["version"], str) or not source["version"] or source["version"] == "current" or not isinstance(source["inputs"], dict):
            continue
        found.add(source["function"])
        sources.extend(source["inputs"].values())
    return found


async def function_uses(session, codes: list[str]) -> dict[str, list[dict]]:
    if not codes:
        return {}
    ontology_id = require_ontology_id(session)
    result = {code: [] for code in codes}

    def add(code, kind, name):
        use = {"kind": kind, "name": name}
        if use not in result[code]:
            result[code].append(use)

    for code, kind, name in await session.execute(
        select(Function.code, FnUse.kind, FnUse.name).join(FnUse, FnUse.function_id == Function.id)
        .where(Function.code.in_(codes), Function.ontology_id == ontology_id, FnUse.ontology_id == ontology_id)
        .order_by(FnUse.id)
    ):
        add(code, kind, name)
    active = (ActionType.ontology_id == ontology_id, ActionType.retired_release_id.is_(None), ActionType.status != TypeStatus.draft)
    definitions = union_all(
        select(ActionType.name, literal("param"), ActionParam.default_text)
        .join(ActionParam, ActionParam.action_type_id == ActionType.id)
        .where(*active, ActionParam.ontology_id == ontology_id),
        select(ActionType.name, literal("edit"), OntologyEditRule.field)
        .join(OntologyEditRule, OntologyEditRule.action_type_id == ActionType.id)
        .where(*active, OntologyEditRule.ontology_id == ontology_id),
        select(Rule.name, literal("rule"), Rule.expr)
        .where(Rule.ontology_id == ontology_id, Rule.version.is_not(None)),
    )
    for name, mode, text in await session.execute(definitions):
        for code in referenced_codes(text, mode) & result.keys():
            add(code, "规则" if mode == "rule" else "动作", name)
    return result
