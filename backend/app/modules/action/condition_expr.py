"""Finite scalar conditions: explicit references and operators, never Python execution."""

import ast
import io
import operator
import re
import tokenize
from typing import Any

from app.core.errors import AppError
from app.modules.functions.runtime import Interpreter, bounded


class Unavailable(ValueError):
    pass


class ConditionInterpreter(Interpreter):
    def __init__(self, values: dict[str, Any], roles: set[str]):
        super().__init__(values, 1)
        self.roles = roles

    def value(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Set):
            return bounded([self.value(item) for item in node.elts])
        if not isinstance(node, ast.Compare):
            return super().value(node)
        self.tick()
        left_node = node.left
        a = self.value(left_node)
        ops = {
            ast.Eq: operator.eq,
            ast.NotEq: operator.ne,
            ast.Lt: operator.lt,
            ast.LtE: operator.le,
            ast.Gt: operator.gt,
            ast.GtE: operator.ge,
        }
        for op, right_node in zip(node.ops, node.comparators, strict=True):
            b = self.value(right_node)
            role = isinstance(left_node, ast.Name) and left_node.id in self.roles
            if role:
                if isinstance(op, ast.Eq | ast.NotEq) and isinstance(b, str):
                    result = b in a
                elif isinstance(op, ast.In | ast.NotIn) and isinstance(b, list):
                    result = any(item in b for item in a)
                else:
                    raise Unavailable("角色条件仅支持角色代码/名称等于或属于集合")
                if isinstance(op, ast.NotEq | ast.NotIn):
                    result = not result
            elif isinstance(op, ast.In | ast.NotIn):
                if not isinstance(b, list) or isinstance(a, list | dict):
                    raise Unavailable("仅支持标量属于字面量集合")
                result = a in b
                if isinstance(op, ast.NotIn):
                    result = not result
            elif type(op) in ops:
                if isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE) and (
                    type(a) not in (int, float) or type(b) not in (int, float)
                ):
                    raise Unavailable("顺序比较只支持有限数值，不推测日期或外部动态上下文")
                result = ops[type(op)](a, b)
            else:
                raise Unavailable("不支持此比较运算")
            if not result:
                return False
            a, left_node = b, right_node
        return True


def evaluate(text: str, references: dict[tuple[str, str], Any]) -> bool:
    if not isinstance(text, str) or not text:
        raise Unavailable("缺少可执行表达式或表达式过长")
    try:
        if len(text.encode("utf-8")) > 4096:
            raise Unavailable("表达式过长")
        raw = list(tokenize.generate_tokens(io.StringIO(text).readline))
        expanded = []
        for token in raw:
            if token.type not in (tokenize.STRING, tokenize.COMMENT) and any(
                c in token.string for c in "≤≥≠×÷∈∉"
            ):
                separated = re.sub(r"([≤≥≠×÷∈∉])", r" \1 ", token.string).strip()
                expanded.extend(tokenize.generate_tokens(io.StringIO(separated).readline))
            else:
                expanded.append(token)
        tokens = [
            t
            for t in expanded
            if t.type not in (tokenize.ENDMARKER, tokenize.NEWLINE, tokenize.NL)
            and not (t.type == tokenize.ERRORTOKEN and t.string.isspace())
        ]
        parts, values, role_refs, bare_literals = [], {}, set(), set()
        i = 0
        aliases = {
            "≤": "<=",
            "≥": ">=",
            "≠": "!=",
            "×": "*",
            "÷": "/",
            "∈": "in",
            "∉": "not in",
            "=": "==",
            "且": "and",
            "或": "or",
            "非": "not",
            "空": "None",
            "真": "True",
            "假": "False",
        }
        while i < len(tokens):
            token = tokens[i]
            if token.type == tokenize.COMMENT:
                raise Unavailable("条件不支持注释或被忽略的语句")
            words = {*aliases, "and", "or", "not", "in"}
            if (
                i
                and token.type in (tokenize.NAME, tokenize.STRING)
                and tokens[i - 1].type in (tokenize.NAME, tokenize.STRING)
                and token.string not in words
                and tokens[i - 1].string not in words
            ):
                raise Unavailable("条件含未支持的自然语言或相邻字面量")
            if i + 2 < len(tokens) and token.type == tokenize.NAME and tokens[i + 1].string == ".":
                ref = (token.string, tokens[i + 2].string)
                if ref not in references:
                    raise Unavailable("缺少可用参数、身份或对象上下文")
                key = f"_ref_{len(values)}"
                value = references[ref]
                bounded(value)
                values[key] = value
                if ref == ("当前用户", "角色"):
                    role_refs.add(key)
                parts.append(key)
                i += 3
                continue
            if token.string == "当前用户":
                key = f"_ref_{len(values)}"
                values[key] = references[("当前用户", "编号")]
                parts.append(key)
                i += 1
                continue
            if token.type == tokenize.STRING:
                parts.append(token.string)
            elif token.string in aliases:
                parts.append(aliases[token.string])
            elif token.type == tokenize.NAME and token.string not in (
                "and",
                "or",
                "not",
                "in",
                "True",
                "False",
                "None",
            ):
                bare_literals.add(token.string)
                parts.append(repr(token.string))
            else:
                parts.append(token.string)
            i += 1
        source = " ".join(parts)
        tree = ast.parse(source, mode="eval")
        if sum(1 for _ in ast.walk(tree)) > 256:
            raise Unavailable("表达式超过语法预算")
        allowed = (
            ast.Expression,
            ast.Constant,
            ast.Name,
            ast.List,
            ast.Set,
            ast.Load,
            ast.BinOp,
            ast.UnaryOp,
            ast.BoolOp,
            ast.Compare,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.And,
            ast.Or,
            ast.Not,
            ast.UAdd,
            ast.USub,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            ast.In,
            ast.NotIn,
        )
        for node in ast.walk(tree):
            if not isinstance(node, allowed):
                raise Unavailable("表达式包含未支持语法")
            if isinstance(node, ast.Compare):
                children = list(ast.walk(node))
                if not any(isinstance(child, ast.Name) and child.id in values for child in children) and any(
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value in bare_literals
                    for child in children
                ):
                    raise Unavailable("未声明名称不能作为独立条件")
        result = ConditionInterpreter(values, role_refs).value(tree.body)
        if type(result) is not bool:
            raise Unavailable("条件必须产生布尔结果")
        return result
    except Unavailable:
        raise
    except (
        AppError,
        SyntaxError,
        tokenize.TokenError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        ZeroDivisionError,
        OverflowError,
        RecursionError,
    ):
        raise Unavailable("表达式无法在受限条件解释器中执行") from None
