"""Nullable scoped keys retain their original semantics without rewriting historical rows."""

import asyncio
import importlib.util
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.common.ontology import RUNTIME_ROLE
from app.modules.ontology.models import OntologyWorkspace
from app.modules.qa.models import QaObjectTypeView, QaSynonymLink

MODELS = (QaObjectTypeView, QaSynonymLink)
KEYS = (("ontology_id", "kind", "release_id"), ("ontology_id", "word", "cr_code"))


def _migration():
    path = Path(__file__).parents[1] / "migrations/versions/qa_0007_nullable_business_keys.py"
    spec = importlib.util.spec_from_file_location("qa_nullable_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(connection):
    with Operations.context(MigrationContext.configure(connection)):
        _migration().upgrade()


def _row(model, ontology_id=1):
    return (
        {"ontology_id": ontology_id, "kind": "nullable-test", "label": "测试", "release_id": None}
        if model is QaObjectTypeView
        else {"ontology_id": ontology_id, "word": "nullable-test", "target": "测试", "cr_code": None}
    )


async def _definitions(conn):
    return (
        await conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid IN ('qa_object_type_view'::regclass,'qa_synonym_link'::regclass) "
                "AND contype='u' ORDER BY conname"
            )
        )
    ).all()


async def _legacy_constraints(conn):
    for model, columns in zip(MODELS, KEYS, strict=True):
        table = model.__tablename__
        name = f"uq_{table}_{'_'.join(columns)}"
        await conn.exec_driver_sql(
            f"ALTER TABLE {table} DROP CONSTRAINT {name}, ADD CONSTRAINT {name} UNIQUE ({', '.join(columns)})"
        )


@pytest.fixture(autouse=True)
def private_database(engine):
    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))


@pytest.mark.parametrize("model", MODELS)
async def test_null_key_is_unique_per_ontology_without_changing_existing_rows(conn, model):
    other = await conn.scalar(
        insert(OntologyWorkspace).values(code="nullable-other", name="测试").returning(OntologyWorkspace.id)
    )
    first = await conn.scalar(insert(model).values(**_row(model)).returning(model.id))
    second = await conn.scalar(insert(model).values(**_row(model, other)).returning(model.id))
    before = (await conn.execute(select(model.__table__).order_by(model.id))).mappings().all()
    with pytest.raises(IntegrityError) as conflict:
        async with conn.begin_nested():
            await conn.execute(insert(model).values(**_row(model)))
    assert conflict.value.orig.sqlstate == "23505"
    assert first != second
    assert (await conn.execute(select(model.__table__).order_by(model.id))).mappings().all() == before


@pytest.mark.parametrize("model", MODELS)
async def test_nullable_key_serializes_concurrent_inserts_without_committed_residue(conn, engine, model):
    await conn.exec_driver_sql(f"SET LOCAL ROLE {RUNTIME_ROLE}")
    await conn.execute(text("SELECT set_config('rengu.ontology_id','1',true)"))
    await conn.execute(insert(model).values(**_row(model)))
    async with engine.connect() as second:
        await second.begin()
        await second.exec_driver_sql(f"SET LOCAL ROLE {RUNTIME_ROLE}")
        await second.execute(text("SELECT set_config('rengu.ontology_id','1',true)"))
        pid = await second.scalar(text("SELECT pg_backend_pid()"))
        pending = asyncio.create_task(second.execute(insert(model).values(**_row(model))))
        try:
            for _ in range(100):
                locked = await conn.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE pid=:pid AND NOT granted)"),
                    {"pid": pid},
                )
                if locked:
                    break
                await asyncio.sleep(0.01)
            assert locked and not pending.done()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await second.rollback()


@pytest.mark.parametrize("duplicate_model", MODELS)
async def test_migration_blocks_duplicate_keys_before_changing_either_constraint(conn, duplicate_model):
    await _legacy_constraints(conn)
    await conn.execute(insert(duplicate_model), [_row(duplicate_model), _row(duplicate_model)])
    definitions = await _definitions(conn)
    before = (
        (await conn.execute(select(duplicate_model.__table__).order_by(duplicate_model.id))).mappings().all()
    )
    savepoint = await conn.begin_nested()
    with pytest.raises(RuntimeError, match="重复业务键"):
        await conn.run_sync(_upgrade)
    await savepoint.rollback()
    assert await _definitions(conn) == definitions
    assert (
        await conn.execute(select(duplicate_model.__table__).order_by(duplicate_model.id))
    ).mappings().all() == before


async def test_migration_requires_visibility_of_all_ontologies(conn):
    await _legacy_constraints(conn)
    definitions = await _definitions(conn)
    with pytest.raises(DBAPIError) as rejected:
        async with conn.begin_nested():
            await conn.exec_driver_sql(f"SET LOCAL ROLE {RUNTIME_ROLE}")
            await conn.execute(text("SELECT set_config('rengu.ontology_id','1',true)"))
            await conn.run_sync(_upgrade)
    assert rejected.value.orig.sqlstate == "42501"
    assert await _definitions(conn) == definitions


async def test_nullable_key_metadata_matches_migrated_database(engine):
    for model in MODELS:
        constraints = [c for c in model.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
        assert len(constraints) == 1
        assert constraints[0].dialect_options["postgresql"]["nulls_not_distinct"] is True
        assert "ontology_id" in constraints[0].columns
    config = Config("alembic.ini")
    config.attributes["database_url"] = engine.url.render_as_string(hide_password=False)
    await asyncio.to_thread(command.check, config)
