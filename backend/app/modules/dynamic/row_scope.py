"""SQL visibility for runtime records referring to real governed object codes."""

from sqlalchemy import and_, false, or_, select

from app.common.object_identity import identity_rows, row_identity_column, row_source, source_columns
from app.common.row_filter import apply_row_filter
from app.common.sql_guard import ensure_governed_tables, governed_tables


async def scoped_instances(session, statement, user, kind_column, identity_column):
    # Read-side visibility only. Internal idempotency/dedupe queries deliberately bypass this helper.
    deleted = getattr(getattr(kind_column, "class_", None), "deleted_at", None)
    if deleted is not None:
        statement = statement.where(deleted.is_(None))
    await ensure_governed_tables(session)
    clauses = []
    for mapping in governed_tables(user.ontology_id, session).values():
        if mapping.inherit or mapping.row_identity not in mapping.table.c:
            continue
        source = row_source(mapping)
        visible = await apply_row_filter(
            select(row_identity_column(mapping, source)), user, mapping.object_type, source_columns(mapping, source), mapping.links
        )
        if mapping.scope_column in source.c:
            visible = visible.where(source.c[mapping.scope_column] == user.ontology_id)
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
        if mapping is None or mapping.row_identity not in mapping.table.c:
            continue
        values = sorted(codes)
        for start in range(0, len(values), 1000):
            identities = identity_rows(mapping).subquery()
            source = row_source(mapping)
            rows = await apply_row_filter(
                select(row_identity_column(mapping, source)), user, kind, source_columns(mapping, source), mapping.links
            )
            if mapping.scope_column in source.c:
                rows = rows.where(source.c[mapping.scope_column] == user.ontology_id)
            statement = select(identities.c.identity).where(
                identities.c.identity.in_(values[start : start + 1000]), identities.c.row_id.in_(rows)
            )
            visible.update((kind, value) for value in await session.scalars(statement))
    return visible
