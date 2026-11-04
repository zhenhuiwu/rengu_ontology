from sqlalchemy import String, select

from app.common.ontology import require_ontology_id
from app.common.relation_resolver import path_metadata, path_rows, resolve_path
from app.common.row_filter import apply_row_filter
from app.common.sql_guard import ensure_governed_tables, governed_tables
from app.core.errors import AppError
from app.modules.governance.policy import enforce
from app.modules.static.lineage import identity
from app.modules.static.models import ObjectType


async def source_mapping(session, code):
    await ensure_governed_tables(session)
    matches = [
        g
        for g in governed_tables(require_ontology_id(session), session).values()
        if g.object_type == code and not g.inherit
    ]
    if len(matches) != 1:
        raise AppError("对象没有唯一已发布的受控绑定", code="BAD_ROW_FILTER", status_code=422)
    return matches[0]


async def options(session):
    await ensure_governed_tables(session)
    oid = require_ontology_id(session)
    names = dict(
        (
            await session.execute(
                select(ObjectType.code, ObjectType.display_name).where(ObjectType.ontology_id == oid)
            )
        ).all()
    )
    output = []
    for mapping in governed_tables(oid, session).values():
        if mapping.inherit:
            continue
        for path in path_metadata(mapping.links):
            hops, column = resolve_path(mapping.links, path)
            if not hops[-1].target_object or not isinstance(column.type, String):
                continue
            output.append(
                {
                    "objType": names.get(mapping.object_type, mapping.object_type),
                    "objectTypeCode": mapping.object_type,
                    "prop": path,
                    "values": [],
                    "path": True,
                    "displayName": " → ".join(path.split(".")),
                    "dataType": "string",
                }
            )
            if len(output) >= 2000:
                return output
    return output


async def values(session, user, code, path, *, selected=None, kw=""):
    mapping = await source_mapping(session, code)
    hops, _ = resolve_path(mapping.links, path)
    if not hops[-1].target_object:
        raise AppError("路径没有已发布目标对象", code="BAD_ROW_FILTER", status_code=422)
    await enforce(
        session,
        user,
        [identity("property", hops[-1].target_object, path.split(".")[-1])],
        "filter",
        unmasked=True,
    )
    statement, column = path_rows(mapping, path)
    statement = await apply_row_filter(statement, user, code, mapping.columns, mapping.links)
    statement = statement.where(column.is_not(None))
    if selected is not None:
        statement = statement.where(column.in_(selected))
    elif kw:
        statement = statement.where(column.contains(kw, autoescape=True))
    rows = await session.scalars(statement.order_by(column).limit(201))
    return [str(getattr(value, "value", value)) for value in rows]
