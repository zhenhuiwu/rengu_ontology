"""Fixed schema scalar paths through actual rule binding/evaluation; no DB or HTTP."""

import ast
import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import AppError
from app.modules.action.condition_expr import Unavailable
from app.modules.dynamic import rule_functions
from app.modules.dynamic.rule_editor import condition_expression
from app.modules.dynamic.rule_runtime import Expression, parse_expression, selected_expression
from app.modules.functions import executor
from app.modules.functions.output_paths import options


async def main():
    output = {"type": "object", "properties": {
        "result": {"type": "integer"},
        "stats": {"type": "object", "properties": {"score": {"type": "number"}},
                  "required": [], "additionalProperties": False},
        "label": {"type": "string"}, "active": {"type": "boolean"}, "empty": {"type": "null"},
        "items": {"type": "array", "items": {"type": "integer"}},
    }, "required": ["result"], "additionalProperties": False}
    snapshot = {"name": "fixture", "code": "", "timeoutS": 2, "runtimeMode": "remote_http", "remote": {
        "inputSchema": {"type": "object", "properties": {"value": {"type": "integer"}},
                        "required": ["value"], "additionalProperties": False}, "outputSchema": output,
    }}
    paths = {tuple(item["path"]) for item in options(snapshot)}
    assert paths == {("result",), ("stats", "score"), ("label",), ("active",), ("empty",)}
    record = (SimpleNamespace(status="published"), SimpleNamespace(snapshot=snapshot))
    session = SimpleNamespace(info={"ontology_id": 8}, execute=AsyncMock(return_value=SimpleNamespace(
        one_or_none=lambda: record)))
    base = 'rule_fn("FN-001", "v1.0.0", 3)'

    async def expression(text):
        tree = parse_expression(text)
        functions = await rule_functions.bind_rule_functions(session, None, tree)
        return Expression(tree, None, None, functions=functions)

    async def run(text, result):
        bound = await expression(text)
        with patch.object(executor, "invoke_snapshot", AsyncMock(return_value=result)) as invoke:
            matched, _ = await bound.run({}, datetime(2026, 11, 3, tzinfo=UTC))
            invoke.assert_awaited_once()
            assert invoke.await_args.kwargs["version"] == "v1.0.0"
            return matched

    with (patch.object(rule_functions, "check_func"),
          patch.object(rule_functions, "authorize", AsyncMock())):
        assert await run(base + '["result"] > 3', {"result": 6}) is True
        assert await run(base + '["stats"]["score"] > 8', {"stats": {"score": 9}}) is True
        for path, result in (("result", {}), ("result", {"result": "6"}), ("stats", {})):
            text = base + ('["stats"]["score"]' if path == "stats" else '["result"]') + " > 0"
            try:
                await run(text, result)
            except Unavailable:
                pass
            else:
                raise AssertionError("Missing/mistyped output became a false condition")
        for text in (base + ' > 0', base + '["unknown"] > 0', base + '["items"][0] > 0',
                     base + '["result"] == True', base + '["result"] > "3"',
                     base + '["label"] > "a"'):
            try:
                await expression(text)
            except AppError:
                pass
            else:
                raise AssertionError(text)
        for path, value, result in (("label", json.dumps("123"), "123"),
                                    ("active", "false", False), ("empty", "null", None)):
            condition = {"expression": base + f'[{json.dumps(path)}]', "op": "=", "value": value}
            text = condition_expression(condition)
            assert await run(text, {path: result}) is True
            replacement = {**condition, "value": json.dumps("456") if path == "label" else value}
            updated = selected_expression({"expr": text, "conds": [condition]},
                                          {"expr": text, "conds": [replacement]})
            if path == "label":
                assert await run(ast.unparse(updated), {path: "456"}) is True
        tree = parse_expression(base + '["result"] > 3')
        assert any(isinstance(node, ast.Subscript) for node in ast.walk(tree))
        bound = await expression(base + '["result"] > 3')
        assert not any(isinstance(node, ast.Subscript) for node in ast.walk(ast.parse(bound.source)))
    print("Rule outputs passed: fixed scalar/nested schema paths, typed comparisons, missing-field failure, "
          "array-index rejection and unchanged restricted condition grammar.")


if __name__ == "__main__":
    asyncio.run(main())
