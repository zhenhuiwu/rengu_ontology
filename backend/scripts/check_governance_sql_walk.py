"""Shared SQL graph traversal checks and in-memory profiles; no database or HTTP."""

import asyncio
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import Column, Integer, MetaData, Table, case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import FromClause, visitors

from app.core.errors import Forbidden
from app.modules.governance import query_guard
from app.modules.static.mapping_pages import candidates


def names(nodes):
    return {getattr(node, "name", None) for node in nodes if isinstance(node, FromClause)}


def mapping_statements(saved):
    session = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
    parent = SimpleNamespace(id=1, display_name="Time", seq=1)
    rows = candidates(SimpleNamespace(session=session), parent, saved, "time")
    conflict = rows.c.data["conflictText"].as_string() != ""
    return (
        select(func.count(), func.sum(case((conflict, 1), else_=0))).select_from(rows),
        select(rows.c.data)
        .order_by(rows.c.bucket, rows.c.seq, rows.c.subseq, rows.c.key)
        .offset(10)
        .limit(10),
    )


def mapping_checks():
    saved = (SimpleNamespace(id=1, code="CR"), SimpleNamespace(target_name="Time"))
    for mode, draft in (("published", None), ("draft", saved)):
        for kind, statement in zip(("count", "page"), mapping_statements(draft), strict=True):
            old_nodes = list(visitors.iterate(statement))
            new_nodes = list(query_guard.unique_nodes(statement))
            assert {id(node) for node in old_nodes} == {id(node) for node in new_nodes}
            assert len(new_nodes) == len({id(node) for node in new_nodes})
            assert names(old_nodes) == names(new_nodes)
            assert len(old_nodes) > 100 * len(new_nodes)
            timings = {}
            for label, walk in (("old", visitors.iterate), ("unique", query_guard.unique_nodes)):
                samples = []
                for _ in range(3):
                    start = perf_counter()
                    names(walk(statement))
                    samples.append((perf_counter() - start) * 1000)
                timings[label] = round(median(samples), 2)
            print(mode, kind, {"visits": len(old_nodes), "unique": len(new_nodes), "ms": timings})


async def guard_checks():
    secret = Table("deep_business", MetaData(), Column("value", Integer))
    first = select(secret.c.value).cte("first")
    second = select(first.c.value).cte("second")
    alias = select(second.c.value).subquery("aliased")
    column_only = select(secret.c.value)  # Implicit FROM comes from its projected column.
    statements = (
        select(alias.c.value),
        select(select(alias.c.value).scalar_subquery()),
        select(1).where(exists(select(alias.c.value))),
        select(func.count(alias.c.value)),
        column_only,
    )
    bound = {"deep_business": SimpleNamespace(object_type="OBJ")}
    async with query_guard.PolicySession() as session:
        user = SimpleNamespace(ontology_id=8)
        session.info["governance_user"] = user
        with (
            patch("app.common.sql_guard.governed_tables", return_value=bound),
            patch("app.common.sql_guard.ensure_governed_tables", new_callable=AsyncMock),
            patch.object(query_guard, "load_definition", AsyncMock(return_value={"resources": [1]})),
            patch.object(query_guard, "check_sql", new_callable=AsyncMock) as checked,
            patch.object(AsyncSession, "execute", new_callable=AsyncMock) as raw,
        ):
            for statement in statements:
                assert "deep_business" in names(query_guard.unique_nodes(statement))
                assert names(visitors.iterate(statement)) == names(query_guard.unique_nodes(statement))
                await session.execute(statement)
                assert checked.await_args.args[3] is bound
            assert checked.await_count == raw.await_count == len(statements)
            token = query_guard.request_session.set({"session": session, "active": False})
            try:
                try:
                    await session.execute(statements[0])
                except Forbidden as error:
                    assert error.code == "GOVERNANCE_CONTEXT_CLOSED"
                else:
                    raise AssertionError("Closed request reached a nested business source")
            finally:
                query_guard.request_session.reset(token)
            assert raw.await_count == len(statements)
            assert "governance_check_running" not in session.info


if __name__ == "__main__":
    mapping_checks()
    asyncio.run(guard_checks())
    print("governance SQL walk: PASS (no database or HTTP)")
