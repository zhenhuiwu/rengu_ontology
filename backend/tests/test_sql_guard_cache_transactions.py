"""Committed, rolled-back and concurrent workspace binding cache behavior."""

import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.modules.biz.governance  # noqa: F401
from app.common import sql_guard
from app.common.ontology import set_ontology_scope
from app.modules.ontology.models import OntologyWorkspace
from app.modules.static.models import ObjectType
from tests.test_sql_guard_ontology import object_type


async def test_uncommitted_binding_never_leaks_to_concurrent_session(engine):
    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        workspace = OntologyWorkspace(code="bound-test-" + uuid4().hex, name="binding test")
        setup.add(workspace)
        await setup.commit()
        oid = workspace.id
    started, checked = asyncio.Event(), asyncio.Event()
    try:

        async def writer():
            async with factory() as session:
                await set_ontology_scope(session, oid)
                assert await sql_guard.load_governed_tables(session) == {}
                session.add(object_type("store", "GuardStore"))
                await session.flush()
                assert "store" in sql_guard.governed_tables(oid, session)
                started.set()
                await checked.wait()
                await session.rollback()

        async def reader():
            await started.wait()
            try:
                async with factory() as session:
                    await set_ontology_scope(session, oid)
                    visible = await session.scalar(select(ObjectType.id).where(ObjectType.code == "store"))
                    assert visible is None
                    assert sql_guard.governed_tables(oid, session) == {}
            finally:
                checked.set()

        await asyncio.gather(writer(), reader())
        async with factory() as session:
            await set_ontology_scope(session, oid)
            await session.execute(select(ObjectType.id))
            assert sql_guard.governed_tables(oid, session) == {}
    finally:
        async with engine.begin() as conn:
            await conn.execute(ObjectType.__table__.delete().where(ObjectType.ontology_id == oid))
            await conn.execute(OntologyWorkspace.__table__.delete().where(OntologyWorkspace.id == oid))


async def test_rolled_back_binding_never_survives_in_next_transaction(engine):
    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        workspace = OntologyWorkspace(code="bound-rollback-" + uuid4().hex, name="binding rollback")
        setup.add(workspace)
        await setup.commit()
        oid = workspace.id
    try:
        async with factory() as writer:
            await set_ontology_scope(writer, oid)
            await sql_guard.load_governed_tables(writer)
            writer.add(object_type("store", "RollbackStore"))
            await writer.flush()
            await writer.rollback()
        async with factory() as reader:
            await set_ontology_scope(reader, oid)
            assert await reader.scalar(select(ObjectType.id).where(ObjectType.code == "store")) is None
            assert sql_guard.governed_tables(oid, reader) == {}
    finally:
        async with engine.begin() as conn:
            await conn.execute(ObjectType.__table__.delete().where(ObjectType.ontology_id == oid))
            await conn.execute(OntologyWorkspace.__table__.delete().where(OntologyWorkspace.id == oid))


async def test_bulk_commit_savepoints_and_same_task_session_isolation(engine):
    from app.db.dialects import get_adapter
    from app.modules.static.models import ConflictStrategy, Lifecycle

    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        workspace = OntologyWorkspace(code="bound-bulk-" + uuid4().hex, name="binding bulk")
        setup.add(workspace)
        await setup.commit()
        oid = workspace.id
    try:
        async with factory() as first, factory() as second:
            await set_ontology_scope(first, oid)
            await set_ontology_scope(second, oid)
            await sql_guard.load_governed_tables(first)
            await sql_guard.load_governed_tables(second)
            first.add(object_type("store", "BoundSavepoint"))
            await first.flush()
            assert "store" in sql_guard.governed_tables(oid, first)
            assert sql_guard.governed_tables(oid, second) == {}
            await first.rollback()
            await first.execute(select(ObjectType.id))
            assert sql_guard.governed_tables(oid, first) == {}
            savepoint = await first.begin_nested()
            first.add(object_type("store", "BoundSavepoint"))
            await first.flush()
            assert "store" in sql_guard.governed_tables(oid, first)
            await savepoint.rollback()
            assert sql_guard.governed_tables(oid, first) == {}
            rows = [
                {
                    "code": "store",
                    "api_name": "BoundBulk",
                    "display_name": "store",
                    "obj_group": "治理专项",
                    "conflict_strategy": ConflictStrategy.user_edit_wins,
                    "lifecycle": Lifecycle.active,
                    "pk_property": "id",
                }
            ]
            await get_adapter(first).upsert(first, ObjectType, rows, ("code",))
            assert "store" in sql_guard.governed_tables(oid, first)
            assert sql_guard.governed_tables(oid, second) == {}
            await first.commit()
            await second.rollback()
            await second.execute(select(ObjectType.id))
            assert "store" in sql_guard.governed_tables(oid, second)
            binding = second.info["governed_binding"]
            second.info["governed_binding"] = (*binding[:3], binding[3] - 31)
            assert sql_guard.governed_tables(oid, second) == {}
            await sql_guard.ensure_governed_tables(second)
            assert "store" in sql_guard.governed_tables(oid, second)
            with pytest.raises(ValueError):
                async with second.begin_nested():
                    await second.execute(text("DELETE FROM object_type WHERE code = 'store'"))
                    assert sql_guard.governed_tables(oid, second) == {}
                    raise ValueError("rollback savepoint")
            assert "store" in sql_guard.governed_tables(oid, second)
            async with second.begin_nested():
                await second.execute(text("DELETE FROM object_type WHERE code = 'store'"))
                assert sql_guard.governed_tables(oid, second) == {}
            assert sql_guard.governed_tables(oid, second) == {}
            await second.rollback()
            await second.execute(select(ObjectType.id))
            assert "store" in sql_guard.governed_tables(oid, second)
            assert sql_guard.governed_tables(oid + 100000, second) == {}
            assert sql_guard.governed_tables(oid) == {}
    finally:
        async with engine.begin() as conn:
            await conn.execute(ObjectType.__table__.delete().where(ObjectType.ontology_id == oid))
            await conn.execute(OntologyWorkspace.__table__.delete().where(OntologyWorkspace.id == oid))


async def test_fresh_scoped_session_loads_qa_context_before_first_query(engine):
    from app.modules.qa.chain import build_context

    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        workspace = OntologyWorkspace(code="bound-context-" + uuid4().hex, name="binding context")
        setup.add(workspace)
        await setup.commit()
        oid = workspace.id
    try:
        async with factory() as writer:
            await set_ontology_scope(writer, oid)
            writer.add(object_type("store", "ContextStore"))
            await writer.commit()
        async with factory() as reader:
            await set_ontology_scope(reader, oid)
            assert not reader.in_transaction()
            context = await build_context(reader)
            assert any("CREATE TABLE store" in ddl for ddl in context["schema"])
    finally:
        async with engine.begin() as conn:
            await conn.execute(ObjectType.__table__.delete().where(ObjectType.ontology_id == oid))
            await conn.execute(OntologyWorkspace.__table__.delete().where(OntologyWorkspace.id == oid))


async def test_concurrent_commit_during_binding_read_never_marks_old_snapshot_fresh(engine, monkeypatch):
    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        workspace = OntologyWorkspace(code="bound-race-" + uuid4().hex, name="binding race")
        setup.add(workspace)
        await setup.commit()
        oid = workspace.id
    try:
        async with factory() as writer:
            await set_ontology_scope(writer, oid)
            writer.add(object_type("store", "RaceStore"))
            await writer.commit()
        async with factory() as reader, factory() as writer:
            await set_ontology_scope(reader, oid)
            await set_ontology_scope(writer, oid)
            await sql_guard.ensure_governed_tables(reader)
            assert "store" in sql_guard.governed_tables(oid, reader)
            reader.info.pop("governed_binding")
            selected, committed = asyncio.Event(), asyncio.Event()
            execute = reader.execute
            calls = 0

            async def blocked_execute(statement, *args, **kwargs):
                nonlocal calls
                result = await execute(statement, *args, **kwargs)
                if statement is sql_guard._BOUND_SQL and calls == 0:
                    calls += 1
                    selected.set()
                    await committed.wait()
                return result

            monkeypatch.setattr(reader, "execute", blocked_execute)

            async def remove():
                await selected.wait()
                await writer.execute(text("DELETE FROM object_type WHERE code = 'store'"))
                await writer.commit()
                committed.set()

            await asyncio.gather(sql_guard.ensure_governed_tables(reader), remove())
            assert calls == 1 and sql_guard.governed_tables(oid, reader) == {}
            assert await reader.scalar(select(ObjectType.id).where(ObjectType.code == "store")) is None
    finally:
        async with engine.begin() as conn:
            await conn.execute(ObjectType.__table__.delete().where(ObjectType.ontology_id == oid))
            await conn.execute(OntologyWorkspace.__table__.delete().where(OntologyWorkspace.id == oid))


async def test_commit_invalidates_reader_that_reloaded_during_uncommitted_delete(engine):
    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as setup:
        workspace = OntologyWorkspace(code="bound-commit-" + uuid4().hex, name="binding commit")
        setup.add(workspace)
        await setup.commit()
        oid = workspace.id
    try:
        async with factory() as writer, factory() as reader:
            await set_ontology_scope(writer, oid)
            await set_ontology_scope(reader, oid)
            writer.add(object_type("store", "CommitStore"))
            await writer.commit()
            await sql_guard.ensure_governed_tables(reader)
            assert "store" in sql_guard.governed_tables(oid, reader)
            await writer.execute(text("DELETE FROM object_type WHERE code = 'store'"))
            assert sql_guard.governed_tables(oid, reader) == {}
            await sql_guard.ensure_governed_tables(reader)
            assert "store" in sql_guard.governed_tables(oid, reader)
            await writer.commit()
            assert sql_guard.governed_tables(oid, reader) == {}
            await sql_guard.ensure_governed_tables(reader)
            assert sql_guard.governed_tables(oid, reader) == {}
    finally:
        async with engine.begin() as conn:
            await conn.execute(ObjectType.__table__.delete().where(ObjectType.ontology_id == oid))
            await conn.execute(OntologyWorkspace.__table__.delete().where(OntologyWorkspace.id == oid))
