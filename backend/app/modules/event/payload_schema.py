"""Bounded nested payload definitions shared by writes and ingress."""
import re
from app.modules.event.ingress_schema import bad

SCALARS = {"字符串", "文本", "枚举", "整数", "金额", "百分比", "数值", "小数", "布尔", "日期", "日期时间"}

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
