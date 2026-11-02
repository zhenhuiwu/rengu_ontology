"""One-hop published FK relation reads, with target row scope applied before lookup."""
import ast
from sqlalchemy import select
from app.common.row_filter import apply_row_filter
from app.common.ontology import require_ontology_id
from app.common.sql_guard import ensure_governed_tables, governed_tables
from app.modules.dynamic.rule_runtime import unavailable
from app.modules.qa.object_source import check_object_read
from app.modules.static.models import DefStatus, LinkType, ObjectStatus, ObjectType


async def read_relation_binding(session, identity):
    """Bind published read-only relations without requiring a writable registry path."""
    if not isinstance(identity, str) or not identity or len(identity) > 128:
        raise unavailable("关系映射标识无效")
    context = (require_ontology_id(session), session.sync_session.get_transaction())
    if session.info.get("rule_relation_read_context") != context:
        session.info["rule_relation_read_context"] = context
        session.info["rule_relation_read_cache"] = {}
    cache = session.info["rule_relation_read_cache"]
    if identity in cache:
        return cache[identity]
    links = list(await session.scalars(select(LinkType).where((LinkType.code == identity) | (LinkType.api_name == identity))))
    if len(links) != 1 or links[0].status != DefStatus.published or links[0].via:
        raise unavailable("关系没有唯一的已发布一跳映射")
    link = links[0]
    objects = list(await session.scalars(select(ObjectType).where(ObjectType.code.in_({link.from_code, link.to_code}))))
    by_code = {obj.code: obj for obj in objects if obj.status == ObjectStatus.published}
    if len(objects) != len({link.from_code, link.to_code}) or set(by_code) != {link.from_code, link.to_code}:
        raise unavailable("关联两端没有唯一的已发布对象定义")
    await ensure_governed_tables(session)
    bindings = list(governed_tables(require_ontology_id(session), session).values())
    bound = []
    for code in (link.from_code, link.to_code):
        matches = [g for g in bindings if g.object_type == code and not g.inherit]
        if len(matches) != 1 or matches[0].source_sql:
            raise unavailable("关联两端没有唯一的本地受控映射")
        bound.append(matches[0])
    src, dst = by_code[link.from_code], by_code[link.to_code]
    sg, dg = bound
    columns = []
    for obj, governed, api in ((src, sg, link.from_prop), (dst, dg, link.to_prop)):
        props = [p for p in obj.properties if p.api_name == api]
        column = (props[0].backing_column or props[0].source_column) if len(props) == 1 else None
        if len(props) != 1 or props[0].is_sensitive or props[0].visibility == "hidden" or props[0].transform or not column or column not in governed.columns or (governed.projection is not None and column not in governed.projection):
            raise unavailable("关联键不可读取或未映射授权列")
        columns.append(governed.columns[column])
    left, right = columns
    target = f"{dg.table.name}.{right.name}"
    if not any(f.target_fullname.removeprefix("public.") == target for f in left.foreign_keys):
        raise unavailable("已发布关系与真实数据库外键不一致")
    result = link, src, sg, dst, dg, left, right
    cache[identity] = result
    return result

async def bind_rule_relations(session, user, tree, obj, gov, *, source_row=None):
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
        link, src, sg, dst, dg, local, target_key = await read_relation_binding(session, relation)
        if src.code != obj.code or sg.table is not gov.table:
            raise unavailable("关联必须从规则对象出发")
        props = [p for p in dst.properties if p.api_name == field]
        column = (props[0].backing_column or props[0].source_column) if len(props) == 1 else None
        if len(props) != 1 or props[0].is_sensitive or props[0].visibility == "hidden" or props[0].transform or not column or column not in dg.table.c or (dg.projection is not None and column not in dg.projection):
            raise unavailable("关联目标字段不可读取或未映射")
        statement = await apply_row_filter(select(target_key, dg.table.c[column]), user, dst.code, dg.columns, dg.links)
        if source_row is not None:
            statement = statement.where(target_key == source_row.get(local.name))
        rows = (await session.execute(statement.limit(2 if source_row is not None else 10001))).all()
        if len(rows) > 10000:
            raise unavailable("关联目标超过10000行，请缩小授权范围")
        values = {}
        for key, value in rows:
            if key in values:
                raise unavailable("关联目标键不唯一")
            values[key] = value
        result[ast.dump(node)] = (local.name, values, f"{link.api_name}.{field}")
    return result
