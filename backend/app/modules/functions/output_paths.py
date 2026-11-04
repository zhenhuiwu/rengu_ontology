"""Schema-bound scalar projections for rule conditions; no array index inference."""

import ast
import json

from app.core.errors import AppError
from app.modules.functions.executor import definition_signature
from app.modules.functions.remote_schema import validate_schema, validate_value

SCALARS = {"string", "integer", "number", "boolean", "null"}


def invalid(message):
    return AppError(message, code="FUNCTION_OUTPUT_PATH_INVALID", status_code=422)


def comparison_value(raw, fallback):
    text = raw.strip()
    if text in ("null", "None", "空"):
        return None
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            raise invalid("函数文本比较值须为完整文本") from None
    if text in ("true", "false"):
        return text == "true"
    return fallback


def access(node):
    current, path = node, []
    while isinstance(current, ast.Subscript):
        path.append(current.slice.value if isinstance(current.slice, ast.Constant) else None)
        current = current.value
    if not (isinstance(current, ast.Call) and isinstance(current.func, ast.Name)
            and current.func.id == "rule_fn"):
        return None
    if any(not isinstance(key, str) or not key or len(key) > 64 for key in path) or len(path) > 8:
        raise invalid("函数返回路径只允许固定对象属性，不支持数组索引或动态取值")
    return current, list(reversed(path))


def options(snapshot):
    if snapshot.get("runtimeMode", "local") != "remote_http":
        _, output = definition_signature(snapshot)
        kind = {"str": "string", "int": "integer", "float": "number", "bool": "boolean",
                "None": "null"}.get(output["type"], "unknown")
        return [{"path": [], "type": kind, "label": "返回值"}]
    schema = snapshot["remote"]["outputSchema"]
    validate_schema(schema)
    result = []

    def visit(node, path):
        if node["type"] in SCALARS:
            result.append({"path": path, "type": node["type"],
                           "label": ".".join(path) or "返回值"})
        elif node["type"] == "object":
            for key, child in node["properties"].items():
                visit(child, [*path, key])
    visit(schema, [])
    return result


def selected_schema(snapshot, path):
    if snapshot.get("runtimeMode", "local") != "remote_http":
        if path:
            raise invalid("该旧函数版本没有字段级输出合同，不能猜测返回路径")
        return None
    schema = snapshot["remote"]["outputSchema"]
    validate_schema(schema)
    for key in path:
        if schema["type"] != "object" or key not in schema["properties"]:
            raise invalid("返回路径不在固定函数版本的输出合同中")
        schema = schema["properties"][key]
    if schema["type"] not in SCALARS:
        raise invalid("规则条件须选择标量返回字段，不能直接比较对象或数组")
    return schema


def project(value, snapshot, path):
    schema = selected_schema(snapshot, path)
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise invalid("函数结果缺少所选返回字段：" + ".".join(path))
        value = value[key]
    if schema is not None:
        validate_value(value, schema, "output." + ".".join(path))
    return value


def comparison(node, parent, schema):
    if schema is None or not isinstance(parent, ast.Compare):
        return
    operands = [parent.left, *parent.comparators]
    for left, op, right in zip(operands, parent.ops, operands[1:], strict=False):
        other = right if left is node else left if right is node else None
        if other is None:
            continue
        try:
            value = ast.literal_eval(other)
        except (ValueError, TypeError, SyntaxError):
            raise invalid("函数标量比较须使用与输出类型匹配的固定值") from None
        kind = schema["type"]
        numeric = kind in {"integer", "number"}
        if (numeric and type(value) not in (int, float)) or (
            not numeric and type(value) is not {"string": str, "boolean": bool, "null": type(None)}[kind]
        ):
            raise invalid("比较值类型与固定函数返回字段不一致")
        if not numeric and not isinstance(op, (ast.Eq, ast.NotEq)):
            raise invalid("文本、布尔或空值返回字段仅支持等于或不等于比较")
