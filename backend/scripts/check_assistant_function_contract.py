"""One local check for the real assistant's inspection-label source; no app/API/DB/model."""
import ast
from pathlib import Path

from app.core.errors import AppError
from app.modules.functions.runtime import run, signature, validate_source

SOURCE = '''def inspection_label(score: int) -> str:
    if score >= 80:
        return "正常"
    else:
        return "需维护"
'''

inputs, output = signature(SOURCE, "inspection_label")
assert inputs == [{"name": "score", "type": "int", "desc": ""}]
assert output["type"] == "str"
validate_source(SOURCE, "inspection_label")
assert run(SOURCE, "inspection_label", {"score": 80}, 5) == "正常"
assert run(SOURCE, "inspection_label", {"score": 79}, 5) == "需维护"
for value in ({"input": {"score": 80}}, {"params": {"score": 80}}, {"context": {"score": 80}}):
    try:
        run(SOURCE, "inspection_label", value, 5)
    except AppError as error:
        assert error.code == "FUNCTION_INPUT_INVALID"
    else:
        raise AssertionError("A wrapper must not silently become a declared score parameter")

try:
    run(SOURCE, "inspection_label", {}, 5)
except AppError as error:
    assert error.code == "FUNCTION_INPUT_INVALID"
else:
    raise AssertionError("A required parameter must not silently become optional")
optional = '''def inspection_label(score: int = None) -> str:
    if score == None:
        return "待检"
    if score >= 85:
        return "正常"
    return "需维护"
'''
for value, expected in (({}, "待检"), ({"score": None}, "待检"), ({"score": 85}, "正常"), ({"score": 84}, "需维护")):
    assert run(optional, "inspection_label", value, 5) == expected
try:
    validate_source(optional.replace("score == None", "score is None"), "inspection_label")
except AppError as error:
    assert error.code == "FUNCTION_UNSUPPORTED"
else:
    raise AssertionError("The contract must not silently permit unsupported identity comparisons")

root = Path(__file__).resolve().parents[1]
tree = ast.parse((root / "app/modules/functions/schemas.py").read_text(encoding="utf-8"))
fields = {
    node.target.id
    for model in tree.body if isinstance(model, ast.ClassDef) and model.name in {"SaveIn", "CreateIn"}
    for node in model.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
}
assert not {"sample_in", "sample_out", "sampleIn", "sampleOut"} & fields
assistant = ast.parse((root / "app/modules/auto_ontology/assistant.py").read_text(encoding="utf-8"))
compile(assistant, "assistant.py", "exec")  # Compile trusted source only; do not import or execute the application.
generate = next(node for node in assistant.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate")
prompts = "".join(node.value.value for node in generate.body if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == "system" and isinstance(node.value, ast.Constant))
assert "键直接绑定形参" in prompts and "必须在函数签名声明默认值" in prompts
assert "不使用 is 或 is not" in prompts and "不凭空增加" in prompts
warning_assignment = next(node for node in ast.walk(generate) if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "list" and ast.unparse(node.targets[0]) == "result['warnings']")
warning = "函数代码未经运行验证，请审阅并使用平台受控测试。"
state = {"warnings": [warning, warning, "other", "other"]}
exec(compile(ast.Module(body=[warning_assignment], type_ignores=[]), "assistant-warning-only", "exec"), {"result": state})
assert state["warnings"] == [warning, "other"]
edit_gate = next(node for node in ast.walk(generate) if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "edit_context" and node.orelse)
new_gate = compile(ast.Module(body=edit_gate.orelse, type_ignores=[]), "assistant-new-function-gate-only", "exec")
for draft in ({"name": "inspection_label", "code": optional}, {"desc": "description only"}, {"name": "inspection_label"}, {"code": optional}):
    exec(new_gate, {"target": "function", "result": {"draft": draft}})
try:
    exec(new_gate, {"target": "function", "result": {"draft": {"name": "inspection_label", "code": optional.replace("score == None", "score is None")}}})
except AppError as error:
    assert error.code == "FUNCTION_UNSUPPORTED"
else:
    raise AssertionError("New function suggestions must reject unsupported syntax before returning a draft")
print("inspection-label contract passed (bounded interpreter only; no API/DB/model)")
