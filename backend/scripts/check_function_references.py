"""Offline real-reference merge and guard contract; no app, DB, HTTP or generated code."""
import ast
import asyncio
import json
from pathlib import Path

from sqlalchemy.dialects import postgresql

from app.modules.functions.references import function_uses, referenced_codes


def binding(code="FN001"):
    return {"function": code, "version": "v1.0.1", "inputs": {"score": {"param": "score"}}}


async def check():
    assert referenced_codes(json.dumps(binding()), "param") == {"FN001"}
    assert referenced_codes(json.dumps({"values": {"status": binding()}}), "edit") == {"FN001"}
    assert referenced_codes(json.dumps({"status": binding()}), "edit") == {"FN001"}
    assert referenced_codes(json.dumps({"status": {"value": binding()}}), "edit") == set()
    assert referenced_codes(json.dumps({**binding(), "version": "current"}), "param") == set()
    assert referenced_codes("old natural-language declaration", "edit") == set()
    assert referenced_codes("rule_fn('FN001', 'v1.0.1', object.score) > 0 AND true", "rule") == {"FN001"}
    assert referenced_codes("'rule_fn(\"FN001\", \"v1.0.1\")'", "rule") == set()

    class Session:
        info = {"ontology_id": 16}

        def __init__(self):
            self.calls = 0

        async def execute(self, statement):
            self.calls += 1
            sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            assert "ontology_id = 16" in sql
            if self.calls == 1:
                assert "function.ontology_id = 16" in sql and "fn_use.ontology_id = 16" in sql
                return [("FN001", "动作", "legacy"), ("FN002", "规则", "legacy rule")]
            for table in ("action_type", "action_param", "ontology_edit_rule", "rule"):
                assert f"{table}.ontology_id = 16" in sql.replace('"', '')
            assert "retired_release_id IS NULL" in sql and "version IS NOT NULL" in sql
            return [
                ("inspection", "param", json.dumps(binding())),
                ("inspection", "edit", json.dumps({"status": binding()})),
                ("inspection rule", "rule", "rule_fn('FN001', 'v1.0.1', object.score) > 0"),
                ("other function", "param", json.dumps(binding("FN999"))),
            ]

    session = Session()
    uses = await function_uses(session, ["FN001", "FN002"])
    assert uses == {
        "FN001": [{"kind": "动作", "name": "legacy"}, {"kind": "动作", "name": "inspection"}, {"kind": "规则", "name": "inspection rule"}],
        "FN002": [{"kind": "规则", "name": "legacy rule"}],
    }
    assert session.calls == 2
    assert await function_uses(session, []) == {} and session.calls == 2
    root = Path(__file__).resolve().parents[1]
    for name in ("service.py", "sections.py"):
        tree = ast.parse((root / "app/modules/functions" / name).read_text(encoding="utf-8"))
        assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "function_uses" for node in ast.walk(tree))
    tree = ast.parse((root / "app/modules/functions/sections.py").read_text(encoding="utf-8"))
    guard = next(node for node in ast.walk(tree) if isinstance(node, ast.If) and ast.unparse(node.test) == "used and old_signature != new_signature")
    assert any(isinstance(node, ast.Raise) for node in ast.walk(guard))
    print("Function current-reference merge and signature guard passed (offline only).")


if __name__ == "__main__":
    asyncio.run(check())
