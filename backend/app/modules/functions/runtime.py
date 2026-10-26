"""Small, bounded interpreter for pure expressions. Never executes Python bytecode."""

import ast
import json
import math
import operator
import time
from typing import Any

from app.core.errors import AppError

MAX_BYTES = 65_536
MAX_NODES = 512
MAX_STEPS = 2_048
MAX_ITEMS = 512


def invalid(message: str, code: str = "FUNCTION_UNSUPPORTED") -> AppError:
    return AppError(message, code=code, status_code=422)


def bounded(value: Any, depth: int = 0, budget: list[int] | None = None) -> Any:
    if budget is None:
        budget = [0, 0]
    budget[0] += 1
    if budget[0] > MAX_STEPS:
        raise invalid("JSON 结构最多 2048 个值", "FUNCTION_LIMIT")
    if depth > 16:
        raise invalid("输入或结果嵌套过深", "FUNCTION_LIMIT")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value.bit_length() > 256:
            raise invalid("整数超过计算范围", "FUNCTION_LIMIT")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise invalid("输入或结果必须是有限数值", "FUNCTION_LIMIT")
    elif isinstance(value, str):
        if "\x00" in value:
            raise invalid("文本不能包含空字符", "VALIDATION_FAILED")
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            raise invalid("文本含无效 Unicode 字符", "VALIDATION_FAILED") from None
        budget[1] += size
        if budget[1] > MAX_BYTES:
            raise invalid("文本超过 64 KiB", "FUNCTION_LIMIT")
    elif isinstance(value, list | dict):
        if len(value) > MAX_ITEMS:
            raise invalid("容器最多 512 项", "FUNCTION_LIMIT")
        if isinstance(value, dict):
            for key in value:
                if not isinstance(key, str):
                    raise invalid("JSON 对象的键必须是文本")
                bounded(key, depth + 1, budget)
            values = value.values()
        else:
            values = value
        for item in values:
            bounded(item, depth + 1, budget)
    else:
        raise invalid("仅支持 JSON 数据")
    return value


def json_value(value: Any) -> Any:
    bounded(value)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_BYTES:
        raise invalid("输入或结果超过 64 KiB", "FUNCTION_LIMIT")
    return value


def definition(source: str, name: str) -> ast.FunctionDef:
    if len(source.encode("utf-8")) > MAX_BYTES:
        raise invalid("源码超过 64 KiB", "FUNCTION_LIMIT")
    try:
        tree = ast.parse(source)
    except (SyntaxError, RecursionError, ValueError):
        raise invalid("代码不是有效的函数定义", "VALIDATION_FAILED") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise invalid("源码最多 512 个语法节点", "FUNCTION_LIMIT")
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != name:
        raise invalid(f"代码须定义且仅定义函数 {name}", "VALIDATION_FAILED")
    fn = functions[0]
    if fn.decorator_list or fn.args.posonlyargs or fn.args.kwonlyargs or fn.args.vararg or fn.args.kwarg:
        raise invalid("不支持装饰器、可变参数或仅限关键字参数")
    if len(fn.args.args) > 32:
        raise invalid("最多 32 个入参", "FUNCTION_LIMIT")
    if any(len(arg.arg) > 64 for arg in fn.args.args):
        raise invalid("参数名最多 64 字符", "VALIDATION_FAILED")
    if len({arg.arg for arg in fn.args.args}) != len(fn.args.args):
        raise invalid("入参名不能重复", "VALIDATION_FAILED")
    return fn


def signature(source: str, name: str) -> tuple[list[dict], dict]:
    fn = definition(source, name)
    inputs = [
        {"name": arg.arg, "type": ast.unparse(arg.annotation) if arg.annotation else "Any", "desc": ""}
        for arg in fn.args.args
    ]
    output = {"name": "result", "type": ast.unparse(fn.returns) if fn.returns else "Any", "desc": ""}
    if any(len(p["type"]) > 128 for p in [*inputs, output]):
        raise invalid("类型说明最多 128 字符", "VALIDATION_FAILED")
    return inputs, output


class Interpreter:
    def __init__(self, env: dict[str, Any], timeout_s: int):
        self.env = dict(env)
        self.steps = 0
        self.deadline = time.perf_counter() + min(timeout_s, 1)

    def tick(self):
        self.steps += 1
        if self.steps > MAX_STEPS or time.perf_counter() > self.deadline:
            raise invalid("函数超过计算预算", "FUNCTION_LIMIT")

    def value(self, node: ast.AST) -> Any:
        self.tick()
        match node:
            case ast.Constant(value=value):
                return bounded(value)
            case ast.Name(id=name):
                if name not in self.env:
                    raise invalid(f"缺少参数或未定义变量：{name}", "FUNCTION_INPUT_INVALID")
                return self.env[name]
            case ast.List(elts=items):
                return bounded([self.value(item) for item in items])
            case ast.Dict(keys=keys, values=values):
                names = [self.value(key) for key in keys]
                if any(not isinstance(key, str) for key in names):
                    raise invalid("结果字典的键必须是文本")
                return bounded(dict(zip(names, (self.value(item) for item in values), strict=True)))
            case ast.Subscript(value=container, slice=index):
                obj, key = self.value(container), self.value(index)
                if not isinstance(obj, dict | list | str) or not isinstance(key, str | int):
                    raise invalid("仅支持 JSON 对象键或数组下标")
                return bounded(obj[key])
            case ast.UnaryOp(op=op, operand=operand):
                value = self.value(operand)
                if isinstance(op, ast.Not):
                    return not value
                if type(value) not in (int, float) or not isinstance(op, ast.UAdd | ast.USub):
                    raise invalid("不支持此一元运算")
                return bounded(value if isinstance(op, ast.UAdd) else -value)
            case ast.BinOp(left=left, op=op, right=right):
                a, b = self.value(left), self.value(right)
                if isinstance(op, ast.Add) and isinstance(a, str) and isinstance(b, str):
                    if len(a) + len(b) > MAX_BYTES:
                        raise invalid("文本超过计算范围", "FUNCTION_LIMIT")
                    return bounded(a + b)
                ops = {
                    ast.Add: operator.add,
                    ast.Sub: operator.sub,
                    ast.Mult: operator.mul,
                    ast.Div: operator.truediv,
                    ast.FloorDiv: operator.floordiv,
                    ast.Mod: operator.mod,
                }
                if type(a) not in (int, float) or type(b) not in (int, float) or type(op) not in ops:
                    raise invalid("只支持有限数值的 + - * / // % 运算")
                return bounded(ops[type(op)](a, b))
            case ast.IfExp(test=test, body=body, orelse=other):
                return self.value(body if self.value(test) else other)
            case ast.BoolOp(op=op, values=values):
                result = self.value(values[0])
                for item in values[1:]:
                    if (isinstance(op, ast.And) and not result) or (isinstance(op, ast.Or) and result):
                        break
                    result = self.value(item)
                return result
            case ast.Compare(left=left, ops=ops, comparators=items):
                a = self.value(left)
                compare = {
                    ast.Eq: operator.eq,
                    ast.NotEq: operator.ne,
                    ast.Lt: operator.lt,
                    ast.LtE: operator.le,
                    ast.Gt: operator.gt,
                    ast.GtE: operator.ge,
                }
                for op, item in zip(ops, items, strict=True):
                    b = self.value(item)
                    if type(op) not in compare:
                        raise invalid("不支持此比较运算")
                    if not compare[type(op)](a, b):
                        return False
                    a = b
                return True
            case _:
                raise invalid(f"不支持语法：{type(node).__name__}")

    def body(self, rows: list[ast.stmt]) -> tuple[bool, Any]:
        for row in rows:
            self.tick()
            match row:
                case ast.Return(value=value):
                    return True, None if value is None else self.value(value)
                case ast.Assign(targets=[ast.Name(id=name)], value=value):
                    self.env[name] = self.value(value)
                case ast.If(test=test, body=body, orelse=other):
                    returned, value = self.body(body if self.value(test) else other)
                    if returned:
                        return True, value
                case ast.Expr(value=ast.Constant(value=str())):
                    pass  # docstring
                case _:
                    raise invalid(f"不支持语句：{type(row).__name__}")
        return False, None


def run(source: str, name: str, inputs: dict[str, Any], timeout_s: int) -> Any:
    json_value(inputs)
    fn = definition(source, name)
    if any(isinstance(node, ast.Constant) and node.value is Ellipsis for node in ast.walk(fn)):
        raise invalid("函数代码尚未实现（...），不能返回登记示例代替执行", "FUNCTION_NOT_IMPLEMENTED")
    tree = ast.parse(source)
    if any(node is not fn and not isinstance(node, ast.FunctionDef) for node in tree.body):
        raise invalid("测试不支持导入或函数外代码")
    allowed = (
        ast.Return,
        ast.Assign,
        ast.If,
        ast.Expr,
        ast.Constant,
        ast.Name,
        ast.List,
        ast.Dict,
        ast.Subscript,
        ast.UnaryOp,
        ast.BinOp,
        ast.IfExp,
        ast.BoolOp,
        ast.Compare,
        ast.Load,
        ast.Store,
        ast.Not,
        ast.UAdd,
        ast.USub,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
    )
    for node in [*fn.body, *fn.args.defaults]:
        if any(not isinstance(child, allowed) for child in ast.walk(node)):
            raise invalid("测试仅支持 return、赋值、if 和受限 JSON 纯计算表达式")
    arg_names = [arg.arg for arg in fn.args.args]
    if set(inputs) - set(arg_names):
        raise invalid("输入包含未声明参数", "FUNCTION_INPUT_INVALID")
    interpreter = Interpreter(inputs, timeout_s)
    try:
        default_args = fn.args.args[-len(fn.args.defaults) :] if fn.args.defaults else []
        for arg, default in zip(default_args, fn.args.defaults, strict=True):
            if arg.arg not in interpreter.env:
                interpreter.env[arg.arg] = interpreter.value(default)
        if missing := set(arg_names) - set(interpreter.env):
            raise invalid("缺少参数：" + "、".join(sorted(missing)), "FUNCTION_INPUT_INVALID")
        returned, output = interpreter.body(fn.body)
        if not returned:
            raise invalid("函数必须显式 return；空实现不能作为成功运行", "FUNCTION_NOT_IMPLEMENTED")
        return json_value(output)
    except (KeyError, IndexError, TypeError, ZeroDivisionError, OverflowError, RecursionError) as exc:
        raise invalid(f"计算失败：{type(exc).__name__}", "FUNCTION_INPUT_INVALID") from None
