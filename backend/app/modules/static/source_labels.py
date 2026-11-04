"""Resolve datasource display labels inside the current scoped session, without seed aliases."""

from sqlalchemy import or_, select
from sqlalchemy.orm import load_only

from app.modules.data.models import Datasource


async def labels(session, sources):
    names = {source for source in sources if source}
    if not names:
        return {}
    rows = list(
        await session.scalars(
            select(Datasource)
            .options(load_only(Datasource.code, Datasource.name, Datasource.type, Datasource.database))
            .where(or_(Datasource.code.in_(names), Datasource.name.in_(names)))
        )
    )
    result = {}
    for source in names:
        matches = [row for row in rows if source in (row.code, row.name)]
        if len(matches) == 1:
            row = matches[0]
            result[source] = {
                "sourceName": row.name,
                "db": row.type.value + (f" · {row.database}" if row.database else ""),
            }
    return result
