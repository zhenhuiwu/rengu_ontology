"""参数映射里的算术表达式（如「安全库存 × 1.5 − 可用库存」）的安全求值。

只认：数字、+ − × ÷ * / 、半角 / 全角括号、名字（元数据里的属性显示名 / API 名，可带「类型.」前缀）。
先把名字换成占位符，再用 ast 解析并白名单校验，最后交给函数运行时的 Interpreter（有步数预算、
只做 int / float 运算）求值；全程不用 eval()。名字取不到、为空或不是数值时不求值，返回原因。
"""

import ast
import math
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from app.core.errors import AppError
from app.modules.functions.runtime import Interpreter

_OPS = {"×": "*", "÷": "/", "−": "-", "＋": "+", "－": "-", "（": "(", "）": ")"}
_OPS.update({c: c for c in "*/+-()"})
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([×÷−＋－（）*/+\-()])|([^\s×÷−＋－（）*/+\-()]+))")
_ALLOWED = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub,
)  # fmt: skip
MAX_LEN = 200


def tokenize(text: str) -> tuple[str, list[str]] | None:
    """→ (Python 表达式, 名字列表)；含不认识的字符或超长时返回 None。"""
    text = (text or "").strip()
    if not text or len(text) > MAX_LEN:
        return None
    out, names, pos = [], [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            return None
        pos = m.end()
        number, op, name = m.groups()
        if number is not None:
            out.append(number)
        elif op is not None:
            out.append(_OPS[op])
        elif name is not None:
            if name not in names:
                names.append(name)
            out.append(f"_v{names.index(name)}")
    return " ".join(out), names


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, str):
        try:
            v = float(value.strip())
        except ValueError:
            return None
        return v if math.isfinite(v) else None
    return None


def evaluate(text: str, resolve: Callable[[str], tuple[bool, Any, str | None]]) -> dict[str, Any]:
    """resolve(name) → (找到, 值, 出处)。
    返回 {ok, value?, inputs[{name,value,from}], reason?}；ok=False 时 value 不给。"""
    parsed = tokenize(text)
    if parsed is None:
        return {"ok": False, "expression": False, "reason": "不是算术表达式"}
    source, names = parsed
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        return {"ok": False, "expression": True, "reason": "表达式语法不完整"}
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED) or (
            isinstance(node, ast.Constant) and type(node.value) not in (int, float)
        ):
            return {"ok": False, "expression": True, "reason": f"不支持的语法：{type(node).__name__}"}
    env, inputs, missing = {}, [], []
    for i, name in enumerate(names):
        found, raw, origin = resolve(name)
        value = _number(raw) if found else None
        if not found:
            missing.append(f"「{name}」未找到对应属性")
        elif value is None:
            missing.append(
                f"「{name}」为空或不是数值" if raw is None or raw == "" else f"「{name}」不是数值：{raw}"
            )
        else:
            env[f"_v{i}"] = value
            inputs.append({"name": name, "value": value, "from": origin})
    if missing:
        return {"ok": False, "expression": True, "inputs": inputs, "reason": "；".join(missing)}
    try:
        value = Interpreter(env, 1).value(tree.body)
    except ZeroDivisionError:
        return {"ok": False, "expression": True, "inputs": inputs, "reason": "除数为 0"}
    except (AppError, ArithmeticError, RecursionError) as exc:
        return {
            "ok": False,
            "expression": True,
            "inputs": inputs,
            "reason": getattr(exc, "message", str(exc)),
        }
    if type(value) not in (int, float) or not math.isfinite(value):
        return {"ok": False, "expression": True, "inputs": inputs, "reason": "结果不是有限数值"}
    return {"ok": True, "expression": True, "value": value, "inputs": inputs}


def coerce(value: float | int, ptype: str) -> float | int:
    """整数类型四舍五入（半数进位）；其余数值保留，整值去掉小数。"""
    if "整数" in ptype or ptype.lower() in ("int", "integer"):
        return math.floor(value + 0.5)
    return int(value) if float(value).is_integer() else round(value, 6)
