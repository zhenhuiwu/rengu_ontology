"""Published relation paths shared by metadata, role writes and row-filter compilation."""

from dataclasses import replace

import sqlglot
from sqlalchemy import and_, exists, select
from sqlalchemy.sql.util import ClauseAdapter
from sqlglot import exp

MAX_HOPS = 4


def resolve_path(links, field):
    from app.common.row_filter import _bad

    parts = field.split(".")
    if not 2 <= len(parts) <= MAX_HOPS + 1:
        raise _bad(field, "关系路径最多4跳")
    hops = []
    for code in parts[:-1]:
        link = links.get(code)
        if link is None:
            raise _bad(field, "关系没有唯一已发布受控映射")
        hops.append(link)
        links = link.target_links
    if len(hops) > 1 and (
        hops[0].ontology_id is None or any(h.ontology_id != hops[0].ontology_id for h in hops)
    ):
        raise _bad(field, "每跳必须来自同一本体的已发布绑定")
    column = hops[-1].target_columns.get(parts[-1])
    if column is None:
        raise _bad(field, "目标属性不在公开白名单")
    return hops, column


def _root_local(link, columns):
    candidates = list(
        {
            id(c): c
            for c in columns.values()
            if getattr(c, "name", None) == getattr(link.local, "name", None)
        }.values()
    )
    if len(candidates) == 1:
        return candidates[0]
    return link.local


def path_predicate(condition, columns, links, attrs):
    from app.common.row_filter import _cond

    hops, terminal = resolve_path(links, condition.field)

    def compile_hop(index, source):
        link = hops[index]
        target = link.target.alias(f"rls_h{index}")
        adapt = ClauseAdapter(target)
        local = _root_local(link, columns) if source is None else source.c[link.local.name]
        clauses = [adapt.traverse(link.target_key) == local]
        source_table = getattr(local, "table", None)
        if link.ontology_id is not None:
            if (
                source_table is None
                or link.source_scope not in source_table.c
                or link.target_scope not in target.c
            ):
                from app.common.row_filter import _bad

                raise _bad(condition.field, "路径缺少本体隔离列")
            clauses += [
                source_table.c[link.source_scope] == link.ontology_id,
                target.c[link.target_scope] == link.ontology_id,
            ]
        elif (
            source_table is not None and link.source_scope in source_table.c and link.target_scope in target.c
        ):
            clauses.append(target.c[link.target_scope] == source_table.c[link.source_scope])
        clauses.append(
            compile_hop(index + 1, target)
            if index + 1 < len(hops)
            else _cond(condition, adapt.traverse(terminal), attrs)
        )
        return exists(select(1).select_from(target).where(and_(*clauses)).correlate_except(target))

    return compile_hop(0, None)


def path_rows(mapping, field):
    """An explicit, scoped join for selectable role values; never parse user SQL."""
    from app.common.object_identity import row_source

    hops, terminal = resolve_path(mapping.links, field)
    source = row_source(mapping)
    root = source
    clauses = []
    for index, link in enumerate(hops):
        target = link.target.alias(f"scope_h{index}")
        adapt = ClauseAdapter(target)
        if link.local.name not in source.c:
            from app.common.row_filter import _bad

            raise _bad(field, "关联键不在受控来源")
        root = root.join(target, adapt.traverse(link.target_key) == source.c[link.local.name])
        if link.ontology_id is not None:
            clauses.extend(
                [
                    source.c[link.source_scope] == link.ontology_id,
                    target.c[link.target_scope] == link.ontology_id,
                ]
            )
        source = target
    column = ClauseAdapter(source).traverse(terminal)
    return select(column).select_from(root).where(*clauses).distinct(), column


async def attach_relations(session, ontology_id):
    from sqlalchemy.orm import raiseload

    from app.common.object_identity import row_source, source_columns
    from app.common.row_filter import LinkPath
    from app.common.sql_guard import governed_tables
    from app.modules.static.models import DefStatus, LinkType, ObjectStatus, ObjectType

    governed = governed_tables(ontology_id, session)
    objects = list(
        await session.scalars(
            select(ObjectType).where(
                ObjectType.ontology_id == ontology_id, ObjectType.status == ObjectStatus.published
            )
        )
    )
    specs = {g.object_type: g for g in governed.values() if not g.inherit}
    rows = {obj.code: obj for obj in objects if obj.code in specs}
    sources = {code: row_source(g) for code, g in specs.items()}
    columns, terminals = {}, {}
    for code, obj in rows.items():
        g, source = specs[code], sources[code]
        available = source_columns(g, source)
        cols = {}
        for prop in obj.properties:
            name = prop.source_column if g.source_sql else prop.backing_column or prop.source_column
            if prop.is_sensitive or prop.visibility == "hidden" or prop.transform or name not in source.c:
                continue
            if g.projection is not None and name not in g.projection:
                continue
            cols[prop.api_name] = source.c[name]
        columns[code] = {**available, **cols}
        terminals[code] = cols
    links = {code: {} for code in rows}
    for relation in await session.scalars(
        select(LinkType)
        .options(raiseload("*"))
        .where(LinkType.ontology_id == ontology_id, LinkType.status == DefStatus.published)
    ):
        frm, to = relation.from_code, relation.to_code
        if relation.via or frm not in rows or to not in rows:
            continue
        local, key = columns[frm].get(relation.from_prop), columns[to].get(relation.to_prop)
        if local is None or key is None or not getattr(local, "name", None) or not getattr(key, "name", None):
            continue
        sg, tg = specs[frm], specs[to]
        if sg.scope_column not in sources[frm].c or tg.scope_column not in sources[to].c:
            continue
        try:
            tree = sqlglot.parse_one(relation.join_sql, read="postgres")
            if (
                not isinstance(tree, exp.EQ)
                or not isinstance(tree.this, exp.Column)
                or not isinstance(tree.expression, exp.Column)
            ):
                continue
            refs = {(c.table, c.name) for c in (tree.this, tree.expression)}
            from_names = {frm, rows[frm].api_name, sg.name}
            to_names = {to, rows[to].api_name, tg.name}
            if not any(refs == {(f, local.name), (t, key.name)} for f in from_names for t in to_names):
                continue
        except (sqlglot.errors.ParseError, sqlglot.errors.TokenError):
            continue
        links[frm][relation.code] = LinkPath(
            relation.code,
            local,
            sources[to],
            key,
            terminals[to],
            target_links=links[to],
            ontology_id=ontology_id,
            source_scope=sg.scope_column,
            target_scope=tg.scope_column,
            target_object=to,
        )
    for name, mapping in list(governed.items()):
        if mapping.object_type in links:
            governed[name] = replace(mapping, links={**mapping.links, **links[mapping.object_type]})
    for name, mapping in list(governed.items()):
        if mapping.inherit and mapping.inherit.parent.name in governed:
            governed[name] = replace(
                mapping, inherit=replace(mapping.inherit, parent=governed[mapping.inherit.parent.name])
            )
    # The request-local overlay retains all original table/projection validation.
    session.info["governance_relations"] = (ontology_id, governed)
    return governed


def path_metadata(links, prefix=(), *, depth=MAX_HOPS):
    if depth <= 0:
        return
    for code, link in sorted(links.items()):
        path = (*prefix, code)
        for field in sorted(link.target_columns):
            yield ".".join((*path, field))
        yield from path_metadata(link.target_links, path, depth=depth - 1)
