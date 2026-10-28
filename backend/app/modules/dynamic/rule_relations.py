"""One-hop published FK relation reads, with target row scope applied before lookup."""
import ast
from sqlalchemy import select
from app.common.row_filter import apply_row_filter
from app.modules.dynamic.rule_runtime import unavailable
from app.modules.integration.action_edits import relation_binding
from app.modules.qa.object_source import check_object_read

async def bind_rule_relations(session, user, tree, obj, gov):
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "related"]
    if not calls:
        return {}
    if obj is None or gov is None:
        raise unavailable("历史事件未保存对象快照，不能读取关联对象")
    if len(calls) > 4:
        raise unavailable("规则最多读取4个一跳关联字段")
    check_object_read(user)
    result = {}
    for node in calls:
        if node.keywords or len(node.args) != 2 or not all(isinstance(n, ast.Constant) and isinstance(n.value, str) for n in node.args):
            raise unavailable("关联读取须指定关系编号和目标属性 API 名")
        relation, field = (n.value for n in node.args)
        link, src, sg, dst, dg, local = await relation_binding(session, relation)
        if src.code != obj.code or sg.table is not gov.table:
            raise unavailable("关联必须从规则对象出发")
        props = [p for p in dst.properties if p.api_name == field]
        column = (props[0].backing_column or props[0].source_column) if len(props) == 1 else None
        if len(props) != 1 or props[0].is_sensitive or props[0].visibility == "hidden" or props[0].transform or not column or column not in dg.table.c or (dg.projection is not None and column not in dg.projection):
            raise unavailable("关联目标字段不可读取或未映射")
        target_key = sg.links[link.code].target_key
        statement = await apply_row_filter(select(target_key, dg.table.c[column]), user, dst.code, dg.columns, dg.links)
        rows = (await session.execute(statement.limit(10001))).all()
        if len(rows) > 10000:
            raise unavailable("关联目标超过10000行，请缩小授权范围")
        values = {}
        for key, value in rows:
            if key in values:
                raise unavailable("关联目标键不唯一")
            values[key] = value
        result[ast.dump(node)] = (local.name, values, f"{link.api_name}.{field}")
    return result
