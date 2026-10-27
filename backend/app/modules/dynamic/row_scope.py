"""SQL visibility for runtime records referring to real governed object codes."""

from sqlalchemy import and_, false, func, or_, select

from app.common.row_filter import apply_row_filter
from app.common.sql_guard import governed_tables


async def scoped_instances(statement, user, kind_column, identity_column):
    clauses = []
    for mapping in governed_tables().values():
        table = mapping.table
        if mapping.inherit or "code" not in table.c or "id" not in table.c:
            continue
        visible = await apply_row_filter(
            select(table.c.code), user, mapping.object_type, mapping.columns, mapping.links
        )
        unique = select(table.c.code).group_by(table.c.code).having(func.count() == 1)
        clauses.append(
            and_(
                kind_column == mapping.object_type, identity_column.in_(visible), identity_column.in_(unique)
            )
        )
    return statement.where(or_(*clauses) if clauses else false())
