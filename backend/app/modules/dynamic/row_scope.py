"""SQL visibility for runtime records referring to real governed object codes."""

from sqlalchemy import and_, false, or_, select

from app.common.object_identity import identity_rows
from app.common.row_filter import apply_row_filter
from app.common.sql_guard import ensure_governed_tables, governed_tables


async def scoped_instances(session, statement, user, kind_column, identity_column):
    await ensure_governed_tables(session)
    clauses = []
    for mapping in governed_tables(user.ontology_id, session).values():
        table = mapping.table
        if mapping.inherit or "id" not in table.c:
            continue
        visible = await apply_row_filter(
            select(table.c.id), user, mapping.object_type, mapping.columns, mapping.links
        )
        identities = identity_rows(mapping).subquery()
        clauses.append(
            and_(
                kind_column == mapping.object_type,
                identity_column.in_(select(identities.c.identity).where(identities.c.row_id.in_(visible))),
            )
        )
    return statement.where(or_(*clauses) if clauses else false())


async def visible_references(session, user, references):
    """Check page references in bounded batches using the same row/unique-code predicate."""
    await ensure_governed_tables(session)
    grouped = {}
    for kind, code in references:
        grouped.setdefault(kind, set()).add(code)
    visible = set()
    mappings = {
        g.object_type: g for g in governed_tables(user.ontology_id, session).values() if not g.inherit
    }
    for kind, codes in grouped.items():
        mapping = mappings.get(kind)
        if mapping is None or "id" not in mapping.table.c:
            continue
        values = sorted(codes)
        for start in range(0, len(values), 1000):
            identities = identity_rows(mapping).subquery()
            rows = await apply_row_filter(
                select(mapping.table.c.id), user, kind, mapping.columns, mapping.links
            )
            statement = select(identities.c.identity).where(
                identities.c.identity.in_(values[start : start + 1000]), identities.c.row_id.in_(rows)
            )
            visible.update((kind, value) for value in await session.scalars(statement))
    return visible
