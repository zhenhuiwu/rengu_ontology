"""Resolve fixed published function snapshots for bounded rule expressions."""
import ast
from sqlalchemy import select
from app.common.auth import check_func
from app.modules.dynamic.rule_runtime import unavailable
from app.modules.functions.models import FnVersion, Function
from app.modules.functions.runtime import signature

async def bind_rule_functions(session, user, tree):
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "rule_fn"]
    if not calls:
        return {}
    check_func(user, "functions", "view")
    if len(calls) > 4:
        raise unavailable("规则最多调用4个固定版本函数")
    result = {}
    for node in calls:
        if node.keywords or len(node.args) < 2 or not all(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in node.args[:2]):
            raise unavailable("函数调用必须指定函数编号和固定版本")
        code, version = (n.value for n in node.args[:2])
        found = (await session.execute(select(Function, FnVersion).join(FnVersion, FnVersion.function_id == Function.id).where(Function.code == code, Function.status == "published", FnVersion.ver == version, FnVersion.is_seed.is_(False)))).one_or_none()
        if not found or not isinstance(found[1].snapshot, dict):
            raise unavailable("固定函数版本未发布或没有真实快照")
        snap = found[1].snapshot
        if not isinstance(snap.get("code"), str) or not isinstance(snap.get("name"), str) or type(snap.get("timeoutS")) is not int:
            raise unavailable("固定函数版本快照不完整")
        args, _ = signature(snap["code"], snap["name"])
        if len(args) != len(node.args) - 2 or any(not isinstance(n, (ast.Attribute, ast.Constant)) for n in node.args[2:]):
            raise unavailable("函数输入须逐项绑定对象/事件字段或固定值")
        result[ast.dump(node)] = (snap, [p["name"] for p in args])
    return result
