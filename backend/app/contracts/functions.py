"""Production calls use one explicitly bound, immutable published version."""

import ast

from sqlalchemy import select

from app.common.auth import check_func
from app.core.errors import AppError
from app.modules.functions.models import FnVersion, Function
from app.modules.functions.runtime import json_value, run, signature


def typed(value, annotation):
    try:
        node = ast.parse(annotation, mode="eval").body
    except (SyntaxError, ValueError):
        raise AppError("函数绑定类型不可校验", code="FUNCTION_TYPE_UNSUPPORTED", status_code=422) from None

    def matches(item, kind):
        if isinstance(kind, ast.Name):
            types = {
                "str": str,
                "int": int,
                "float": (int, float),
                "bool": bool,
                "dict": dict,
                "list": list,
                "None": type(None),
            }
            if kind.id == "Any":
                return True
            if kind.id in types:
                expected = types[kind.id]
                return type(item) in expected if isinstance(expected, tuple) else type(item) is expected
        if isinstance(kind, ast.Constant) and kind.value is None:
            return item is None
        if isinstance(kind, ast.BinOp) and isinstance(kind.op, ast.BitOr):
            return matches(item, kind.left) or matches(item, kind.right)
        if isinstance(kind, ast.Subscript) and isinstance(kind.value, ast.Name):
            name = kind.value.id
            if name in ("list", "List"):
                return isinstance(item, list) and all(matches(v, kind.slice) for v in item)
            if name in ("dict", "Dict") and isinstance(kind.slice, ast.Tuple) and len(kind.slice.elts) == 2:
                return isinstance(item, dict) and all(
                    matches(k, kind.slice.elts[0]) and matches(v, kind.slice.elts[1]) for k, v in item.items()
                )
            if name == "Optional":
                return item is None or matches(item, kind.slice)
        raise AppError("函数绑定类型不在受控JSON类型中", code="FUNCTION_TYPE_UNSUPPORTED", status_code=422)

    if sum(1 for _ in ast.walk(node)) > 64:
        raise AppError("函数类型过于复杂", code="FUNCTION_TYPE_UNSUPPORTED", status_code=422)
    if not matches(value, node):
        raise AppError("函数输入或结果不符合绑定版本的类型", code="FUNCTION_TYPE_INVALID", status_code=422)


async def invoke_version(session, user, code: str, version: str, inputs: dict):
    check_func(user, "functions", "view")
    if not isinstance(code, str) or not isinstance(version, str) or not version or version == "current":
        raise AppError("函数调用必须绑定明确的已发布版本", code="FUNCTION_VERSION_REQUIRED", status_code=422)
    record = (
        await session.execute(
            select(Function, FnVersion)
            .join(FnVersion, FnVersion.function_id == Function.id)
            .where(Function.code == code, FnVersion.ver == version)
            .with_for_update(read=True)
        )
    ).one_or_none()
    if record is None or record[0].status != "published":
        raise AppError("绑定函数或已发布版本不可用", code="FUNCTION_VERSION_UNAVAILABLE", status_code=409)
    snapshot = record[1].snapshot
    if not isinstance(snapshot, dict) or not {"name", "code", "timeoutS"} <= snapshot.keys():
        raise AppError("函数版本缺少可执行快照", code="FUNCTION_VERSION_UNAVAILABLE", status_code=409)
    if (
        not isinstance(snapshot["name"], str)
        or not isinstance(snapshot["code"], str)
        or type(snapshot["timeoutS"]) is not int
        or not 1 <= snapshot["timeoutS"] <= 120
    ):
        raise AppError("函数版本执行配置无效", code="FUNCTION_VERSION_UNAVAILABLE", status_code=409)
    declared, output = signature(snapshot["code"], snapshot["name"])
    for field in declared:
        if field["name"] in inputs:
            typed(inputs[field["name"]], field["type"])
    result = json_value(run(snapshot["code"], snapshot["name"], inputs, snapshot["timeoutS"]))
    typed(result, output["type"])
    return result
