"""Bounded JSON schema subset for remote computation; no executable annotations."""

import re

from app.core.errors import AppError
from app.modules.functions.runtime import json_value

TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
         "null": type(None), "object": dict, "array": list}
KEY = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")


def invalid(message, path="schema"):
    return AppError(message, code="FUNCTION_SCHEMA_INVALID", status_code=422,
                    details={"field": path})


def validate_schema(schema, *, inputs=False):
    json_value(schema)
    count = 0

    def visit(node, depth):
        nonlocal count
        count += 1
        if count > 128 or depth > 8 or not isinstance(node, dict):
            raise invalid("函数 schema 超过结构预算")
        allowed = {"type", "description", "properties", "required", "additionalProperties", "items", "enum"}
        if set(node) - allowed or node.get("type") not in TYPES:
            raise invalid("函数 schema 仅支持受控 JSON 类型及 properties/required/items/enum")
        kind = node["type"]
        if "description" in node and not isinstance(node["description"], str):
            raise invalid("schema 说明须为文本")
        if kind == "object":
            props, required = node.get("properties"), node.get("required", [])
            if (not isinstance(props, dict) or len(props) > 32
                or any(not KEY.fullmatch(key) for key in props)
                or not isinstance(required, list) or any(not isinstance(key, str) for key in required)
                or len(set(required)) != len(required) or set(required) - set(props)
                or node.get("additionalProperties") is not False):
                raise invalid("对象 schema 须明确字段、必填项并禁止额外字段")
            for child in props.values():
                visit(child, depth + 1)
        elif {"properties", "required", "additionalProperties"} & set(node):
            raise invalid("仅对象 schema 可声明字段")
        if kind == "array":
            visit(node.get("items"), depth + 1)
        elif "items" in node:
            raise invalid("仅数组 schema 可声明 items")
        if "enum" in node:
            if kind in ("object", "array"):
                raise invalid("枚举仅支持标量 JSON 类型")
            if not isinstance(node["enum"], list) or not 1 <= len(node["enum"]) <= 64:
                raise invalid("枚举须为1–64个 JSON 值")
            for value in node["enum"]:
                validate_value(value, {k: v for k, v in node.items() if k != "enum"})

    visit(schema, 0)
    if inputs and schema["type"] != "object":
        raise invalid("函数输入 schema 须为对象")
    return schema


def validate_value(value, schema, path="value"):
    json_value(value)
    kind = schema["type"]
    expected = TYPES[kind]
    if type(value) not in (expected if isinstance(expected, tuple) else (expected,)):
        raise invalid("函数输入或结果不符合声明类型", path)
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        raise invalid("函数值不在声明枚举中", path)
    if kind == "object":
        props = schema["properties"]
        if set(value) - set(props) or set(schema.get("required", [])) - set(value):
            raise invalid("函数对象含未声明字段或缺少必填字段", path)
        for key, item in value.items():
            validate_value(item, props[key], f"{path}.{key}")
    elif kind == "array":
        for index, item in enumerate(value):
            validate_value(item, schema["items"], f"{path}[{index}]")


def annotation(schema):
    kind = schema["type"]
    return {"string": "str", "integer": "int", "number": "float", "boolean": "bool",
            "null": "None", "object": "dict[str, Any]"}.get(kind) or f"list[{annotation(schema['items'])}]"


def signature(remote):
    source, output = remote["inputSchema"], remote["outputSchema"]
    validate_schema(source, inputs=True)
    validate_schema(output)
    inputs = [{"name": name, "type": annotation(field), "desc": field.get("description", "")}
              for name, field in source["properties"].items()]
    return inputs, {"name": "result", "type": annotation(output), "desc": output.get("description", "")}
