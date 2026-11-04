"""Check the fields used by SQL, filters, grouping and sorting before executing them."""

from contextvars import ContextVar

import sqlglot
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import FromClause
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from app.core.errors import Forbidden
from app.modules.governance.policy import enforce, load_definition
from app.modules.static.lineage import identity

request_session = ContextVar("governance_request_session", default=None)


def unique_nodes(expression):
    """Visit shared CTE/subquery expressions once without skipping nested sources."""
    pending, seen = [expression], set()
    while pending:
        node = pending.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        pending.extend(node.get_children())


def bind_request_context(session, user, request):
    """Independent request sessions use the caller's header and authenticated identity, never model input."""
    session.info["data_purpose"] = request.headers.get("X-Data-Purpose") or None
    session.info["governance_user"] = user


async def property_resources(session):
    from sqlalchemy import select

    from app.common.ontology import require_ontology_id
    from app.modules.static.models import ObjectType, Property

    oid = require_ontology_id(session)
    cached = session.info.get("governance_columns")
    if cached and cached[0] == oid:
        return cached[1]
    rows = await session.execute(
        select(ObjectType.code, Property.api_name, Property.backing_column, Property.source_column)
        .join(Property, Property.object_type_id == ObjectType.id)
        .where(ObjectType.ontology_id == oid, Property.ontology_id == oid)
    )
    mapping = {}
    for code, api, backing, source in rows:
        for field in {api, backing, source} - {None, ""}:
            mapping.setdefault((code, field), set()).add(identity("property", code, api))
    session.info["governance_columns"] = oid, mapping
    return mapping


async def check_sql(session, user, tree, governed, *, allow_masked=False):
    if not (await load_definition(session))["resources"]:
        return
    properties = computed_resources(await property_resources(session), governed.values())
    operations = {"read": set(), "filter": set(), "aggregate": set()}
    for scope in traverse_scope(tree):
        tables = {
            alias: governed[source.name]
            for alias, (_, source) in scope.selected_sources.items()
            if isinstance(source, exp.Table) and source.name in governed
        }
        aggregate = any(isinstance(n, exp.AggFunc) for n in scope.expression.walk())
        for mapping in tables.values():
            operations["aggregate" if aggregate else "read"].add(identity("object", mapping.object_type))
        for column in scope.columns:
            possible = (
                [tables[column.table]]
                if column.table in tables
                else [g for g in tables.values() if column.name in g.table.c]
            )
            resources = {r for g in possible for r in properties.get((g.object_type, column.name), ())}
            operation = "read"
            parent = column.parent
            while parent is not None and parent is not scope.expression:
                if isinstance(parent, (exp.Where, exp.Having, exp.Group, exp.Order, exp.Join, exp.If)):
                    operations["filter"].update(resources)
                if isinstance(parent, exp.AggFunc):
                    operation = "aggregate"
                parent = parent.parent
            operations[operation].update(resources)
        # Bare SELECT * is not included in scope.stars; COUNT(*) is deliberately not a projection star.
        stars = [s for s in scope.expression.selects if isinstance(s, exp.Star)] + list(scope.stars)
        for star in stars:
            selected = (
                [tables[star.table]]
                if isinstance(star, exp.Column) and star.table in tables
                else tables.values()
            )
            for mapping in selected:
                for field in mapping.table.c:
                    operations["read"].update(properties.get((mapping.object_type, field.name), ()))
    for operation, resources in operations.items():
        decisions = await enforce(session, user, resources, operation)
        if not allow_masked and any(d["mask"] != "none" for d in decisions.values()):
            raise Forbidden(
                "掩码属性不能通过SQL读取明文，请使用对象读取接口", code="GOVERNANCE_MASK_REQUIRED"
            )


def computed_resources(properties, mappings):
    """Protect registered expression inputs as well as computed output aliases."""
    output = {key: set(value) for key, value in properties.items()}
    for mapping in mappings:
        for name, expression in mapping.columns.items():
            resources = properties.get((mapping.object_type, name), ())
            for part in unique_nodes(expression):
                field = getattr(part, "name", None)
                if field and resources:
                    output.setdefault((mapping.object_type, field), set()).update(resources)
    return output


async def check_statement(statement, user, object_type, columns):
    from types import SimpleNamespace

    context = request_session.get()
    if context is None:
        return
    if not context["active"]:
        raise Forbidden("数据请求上下文已结束", code="GOVERNANCE_CONTEXT_CLOSED")
    session = context["session"]
    if not (await load_definition(session))["resources"]:
        return
    from app.common.sql_guard import ensure_governed_tables, governed_tables

    await ensure_governed_tables(session)
    aliases = {
        g.object_type
        for g in governed_tables(user.ontology_id, session).values()
        if object_type == g.object_type or object_type in g.aliases
    }
    if len(aliases) == 1:
        object_type = next(iter(aliases))
    properties = computed_resources(
        await property_resources(session), [SimpleNamespace(object_type=object_type, columns=columns)]
    )

    def fields(expressions):
        return {
            r
            for expression in expressions
            if expression is not None
            for part in unique_nodes(expression)
            for r in properties.get((object_type, getattr(part, "name", None)), ())
        }

    selected = fields(statement.selected_columns)
    constraints = fields([statement.whereclause, *statement._group_by_clauses, *statement._order_by_clauses])
    await enforce(session, user, [identity("object", object_type)], "read")
    decisions = await enforce(session, user, selected, "read")
    if not statement.get_execution_options().get("governance_masked") and any(
        d["mask"] != "none" for d in decisions.values()
    ):
        raise Forbidden("该数据含掩码字段，请使用受控对象读取", code="GOVERNANCE_MASK_REQUIRED")
    if constraints:
        await enforce(session, user, constraints, "filter", unmasked=True)
    aggregate = any(
        str(getattr(part, "name", "")).lower() in {"sum", "count", "avg", "min", "max"}
        for part in unique_nodes(statement)
    )
    if aggregate:
        await enforce(session, user, selected | {identity("object", object_type)}, "aggregate", unmasked=True)


class PolicySession(AsyncSession):
    async def scalar(self, statement, params=None, *, execution_options=None, bind_arguments=None, **kwargs):
        result = await self.execute(
            statement,
            params=params,
            execution_options=execution_options or {},
            bind_arguments=bind_arguments,
            **kwargs,
        )
        return result.scalar()

    async def execute(self, statement, *args, **kwargs):
        user = self.info.get("governance_user")
        if (
            user is not None
            and not self.info.get("governance_check_running")
            and getattr(statement, "is_select", False)
        ):
            from app.common.sql_guard import ensure_governed_tables, governed_tables

            names = {
                getattr(node, "name", None)
                for node in unique_nodes(statement)
                if isinstance(node, FromClause)
            }
            known = set(governed_tables()) | set(self.info.get("governed_dynamic", {}))
            if names & known:
                context = request_session.get()
                if context is not None and context["session"] is self and not context["active"]:
                    raise Forbidden("数据请求上下文已结束", code="GOVERNANCE_CONTEXT_CLOSED")
                self.info["governance_check_running"] = True
                try:
                    if (await load_definition(self))["resources"]:
                        await ensure_governed_tables(self)
                        bound = governed_tables(user.ontology_id, self)
                        try:
                            sql = str(
                                statement.compile(dialect=postgresql.dialect(paramstyle="numeric_dollar"))
                            )
                            tree = sqlglot.parse_one(sql, read="postgres")
                        except (ValueError, sqlglot.errors.ParseError, sqlglot.errors.TokenError) as exc:
                            raise Forbidden(
                                "无法确认查询用途与属性范围", code="GOVERNANCE_QUERY_UNRESOLVED"
                            ) from exc
                        await check_sql(
                            self,
                            user,
                            tree,
                            bound,
                            allow_masked=statement.get_execution_options().get("governance_masked", False),
                        )
                finally:
                    self.info.pop("governance_check_running", None)
        return await super().execute(statement, *args, **kwargs)
