"""Resolve fixed published function snapshots for bounded rule expressions."""
import ast

from sqlalchemy import select

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.modules.dynamic.rule_runtime import unavailable
from app.modules.functions.executor import authorize, definition_signature
from app.modules.functions.models import FnVersion, Function
from app.modules.functions.output_paths import comparison, selected_schema


async def bind_rule_functions(session, user, tree):
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "rule_fn"]
    if not calls:
        return {}
    check_func(user, "functions", "view")
    if len(calls) > 4:
        raise unavailable("规则最多调用4个固定版本函数")
    result = {}
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in calls:
        if node.keywords or len(node.args) < 2 or not all(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in node.args[:2]):
            raise unavailable("函数调用必须指定函数编号和固定版本")
        code, version = (n.value for n in node.args[:2])
        await authorize(session, user, code)
        found = (await session.execute(select(Function, FnVersion).join(FnVersion, FnVersion.function_id == Function.id).where(Function.code == code, Function.status == "published", FnVersion.ver == version, FnVersion.is_seed.is_(False)))).one_or_none()
        if not found or not isinstance(found[1].snapshot, dict):
            raise unavailable("固定函数版本未发布或没有真实快照")
        snap = found[1].snapshot
        if not isinstance(snap.get("code"), str) or not isinstance(snap.get("name"), str) or type(snap.get("timeoutS")) is not int:
            raise unavailable("固定函数版本快照不完整")
        args, _ = definition_signature(snap)
        if len(args) != len(node.args) - 2 or any(not isinstance(n, (ast.Attribute, ast.Constant)) for n in node.args[2:]):
            raise unavailable("函数输入须逐项绑定对象/事件字段或固定值")
        selection, path = node, []
        while isinstance(parents.get(selection), ast.Subscript) and parents[selection].value is selection:
            selection = parents[selection]
            if not isinstance(selection.slice, ast.Constant) or not isinstance(selection.slice.value, str):
                raise unavailable("函数返回路径仅支持固定对象属性，不推测数组索引")
            path.append(selection.slice.value)
        if len(path) > 8:
            raise unavailable("函数返回路径超过8层")
        schema = selected_schema(snap, path)
        comparison(selection, parents.get(selection), schema)
        result[ast.dump(selection)] = ({**snap, "_functionCode": code, "_functionVersion": version,
                                       "_outputPath": path, "_ontologyId": require_ontology_id(session)},
                                      [p["name"] for p in args])
    return result
