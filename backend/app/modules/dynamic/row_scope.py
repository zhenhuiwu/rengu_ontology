"""SQL visibility for runtime records referring to real governed object codes."""

from sqlalchemy import and_, false, func, literal, or_, select

from app.common.row_filter import apply_row_filter
from app.common.sql_guard import governed_tables


async def scoped_instances(statement, user, kind_column, identity_column):
    clauses = []
    for mapping in governed_tables(user.ontology_id).values():
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


async def visible_references(session, user, references):
    """Check page references in bounded batches using the same row/unique-code predicate."""
    grouped = {}
    for kind, code in references:
        grouped.setdefault(kind, set()).add(code)
    visible = set()
    mappings = {g.object_type: g for g in governed_tables(user.ontology_id).values() if not g.inherit}
    for kind, codes in grouped.items():
        mapping = mappings.get(kind)
        if mapping is None or "code" not in mapping.table.c or "id" not in mapping.table.c:
            continue
        values = sorted(codes)
        for start in range(0, len(values), 1000):
            statement = select(mapping.table.c.code).where(
                mapping.table.c.code.in_(values[start : start + 1000])
            )
            statement = await scoped_instances(statement, user, literal(kind), mapping.table.c.code)
            visible.update((kind, value) for value in await session.scalars(statement))
    return visible
