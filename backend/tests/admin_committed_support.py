"""Committed test-only approval sources for the production independent read-only counter."""

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import set_ontology_scope
from tests.helpers.workspace import TEST_DB


@pytest.fixture
async def committed_approval_rows(engine):
    assert engine.url.database == TEST_DB == "rengu_fix_admin_20261027"
    recorded = []

    async def commit(*rows):
        async with AsyncSession(engine, expire_on_commit=False) as isolated:
            await set_ontology_scope(isolated, 1)
            isolated.add_all(rows)
            await isolated.commit()
            recorded.extend((type(row), row.code) for row in rows)

    yield commit
    async with AsyncSession(engine) as isolated:
        await set_ontology_scope(isolated, 1)
        for model, code in reversed(recorded):
            await isolated.execute(delete(model).where(model.code == code))
        await isolated.commit()
