"""sql_guard.governed_tables resolves governed tables per ontology workspace from its own object types."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.biz.governance  # noqa: F401 - registers store / biz_order / order_line / product
from app.common.auth import CurrentUser
from app.common.ontology import set_ontology_scope
from app.common.sql_guard import SqlRejected, governed_tables, load_governed_tables, row_filters_for
from app.modules.ontology.models import OntologyWorkspace
from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectType, Property


def object_type(code: str, api: str) -> ObjectType:
    return ObjectType(
        code=code,
        api_name=api,
        display_name=code,
        obj_group="治理专项",
        conflict_strategy=ConflictStrategy.user_edit_wins,
        lifecycle=Lifecycle.active,
        pk_property="id",
        properties=[
            Property(
                api_name="id",
                display_name="编号",
                data_type="string",
                prop_group="标识",
                source_column="id",
                description="",
                is_pk=True,
                visibility="normal",
            )
        ],
    )


async def define(session, codes):
    existing = set(await session.scalars(select(ObjectType.code)))
    for code in codes:
        if code not in existing:
            session.add(object_type(code, f"Guard1{code.title().replace('_', '')}"))
    await session.flush()


async def test_registry_and_default_workspace(session):
    assert {"store", "biz_order", "order_line", "product"} <= governed_tables().keys()
    await define(session, ("store", "order", "order_line", "product"))
    codes = set(await session.scalars(select(ObjectType.code)))
    resolved = await load_governed_tables(session)
    assert {g.object_type for g in resolved.values()} <= codes
    assert {"store", "biz_order", "order_line", "product"} <= resolved.keys()
    assert governed_tables(1, session) == resolved
    assert governed_tables(0) == {} and governed_tables("1") == {}  # type: ignore[arg-type]


async def test_second_workspace_only_governs_its_object_types(session, conn):
    await define(session, ("store", "order", "order_line", "product"))
    workspace = OntologyWorkspace(code="guard-second", name="guard second")
    session.add(workspace)
    await session.flush()
    ws = workspace.id
    assert governed_tables(ws) == {}  # nothing loaded yet: never wider than the legacy rule
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as other:
        await set_ontology_scope(other, ws)
        assert await load_governed_tables(other) == {}
        with pytest.raises(SqlRejected):
            await row_filters_for(CurrentUser(id="U-guard", name="g", roles=(), ontology_id=ws), other)
        # Defining an object type refreshes the binding on flush; order_line follows its parent order.
        other.add(object_type("store", "GuardStore"))
        await other.flush()
        assert set(governed_tables(ws, other)) == {"store"}
        other.add(object_type("order", "GuardOrder"))
        await other.flush()
        assert set(governed_tables(ws, other)) == {"store", "biz_order", "order_line"}
        filters = await row_filters_for(CurrentUser(id="U-guard", name="g", roles=(), ontology_id=ws), other)
        assert set(filters) == {"store", "biz_order", "order_line"}
        assert all(params[-1] == ws for _sql, params in filters.values())
        await other.rollback()
    # Workspace 1 keeps its own full binding, unaffected by the second workspace.
    assert "product" in governed_tables(1, session)
