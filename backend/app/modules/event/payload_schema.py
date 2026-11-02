"""Bounded nested payload definitions shared by writes and ingress."""
import re
from app.modules.event.ingress_schema import bad

SCALARS = {"字符串", "文本", "枚举", "整数", "金额", "百分比", "数值", "小数", "布尔", "日期", "日期时间"}


def field_paths(fields, *, typed=False):
    """Explicit object paths only; arrays require an explicit aggregation contract."""
    from types import SimpleNamespace

    result = {}

    def add(field, prefix="", label_prefix=""):
        if isinstance(field, dict):
            field = SimpleNamespace(**field)
        path, label = prefix + field.api, label_prefix + field.name
        result[path] = (label, field.type) if typed else label
        if field.type == "对象":
            for child in (getattr(field, "schema", None) or {}).get("fields", []):
                add(SimpleNamespace(**child), path + ".", label + ".")

    for field in fields:
        add(field)
    return result


def payload_value(payload, path, fields=None):
    """Read declared values, projecting containers so unknown historical keys cannot escape."""
    from types import SimpleNamespace

    def project(value, kind, schema):
        if value is None:
            return None
        if kind == "对象":
            if not isinstance(value, dict) or not isinstance(schema, dict) or set(schema) != {"fields"}:
                raise KeyError(path)
            return {child["api"]: project(value[child["api"]], child["type"], child["schema"])
                    for child in schema["fields"] if child["api"] in value}
        if kind == "数组":
            if not isinstance(value, list) or not isinstance(schema, dict) or set(schema) != {"item"}:
                raise KeyError(path)
            item = schema["item"]
            return [project(item_value, item["type"], item["schema"]) for item_value in value]
        if isinstance(value, (dict, list)):
            raise KeyError(path)  # A scalar definition never grants access to arbitrary JSON.
        return value

    value = payload
    definition, remaining = None, fields
    for key in path.split("."):
        if remaining is not None:
            matches = [SimpleNamespace(**field) if isinstance(field, dict) else field for field in remaining]
            matches = [field for field in matches if field.api == key]
            if len(matches) != 1:
                raise KeyError(path)
            definition = matches[0]
            schema = getattr(definition, "schema", None)
            remaining = schema.get("fields", []) if definition.type == "对象" and isinstance(schema, dict) else []
        if not isinstance(value, dict) or key not in value:
            raise KeyError(path)
        value = value[key]
    return project(value, definition.type, getattr(definition, "schema", None)) if definition is not None else project(value, None, None)

def validate_definition(kind, schema, depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > 4 or budget[0] > 100:
        raise bad("载荷定义最多嵌套4层、包含100项", "EVENT_PAYLOAD_CONFIG_INVALID", 422)
    if not isinstance(kind, str):
        raise bad("载荷字段类型无效", "EVENT_PAYLOAD_CONFIG_INVALID", 422)
    if kind in SCALARS:
        if schema is not None:
            raise bad("标量字段不能包含嵌套定义", "EVENT_PAYLOAD_CONFIG_INVALID", 422)
        return
    if not isinstance(schema, dict):
        raise bad("对象或数组需要嵌套定义", "EVENT_PAYLOAD_CONFIG_INVALID", 422)
    if kind == "对象" and set(schema) == {"fields"} and isinstance(schema["fields"], list) and 0 < len(schema["fields"]) <= 100:
        seen = set()
        for field in schema["fields"]:
            if not isinstance(field, dict) or set(field) != {"name", "api", "type", "required", "schema"}:
                raise bad("对象字段定义无效", "EVENT_PAYLOAD_CONFIG_INVALID", 422)
            name, api = field["name"], field["api"]
            if not isinstance(name, str) or not name.strip() or len(name) > 64 or not isinstance(api, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", api) or api in seen or type(field["required"]) is not bool:
                raise bad("对象字段名称或 API 名无效或重复", "EVENT_PAYLOAD_CONFIG_INVALID", 422)
            seen.add(api)
            validate_definition(field["type"], field["schema"], depth + 1, budget)
        return
    if kind == "数组" and set(schema) == {"item"} and isinstance(schema["item"], dict) and set(schema["item"]) == {"type", "schema"}:
        validate_definition(schema["item"]["type"], schema["item"]["schema"], depth + 1, budget)
        return
    raise bad("载荷嵌套定义无效", "EVENT_PAYLOAD_CONFIG_INVALID", 422)
