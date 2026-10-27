"""Explicit test-only workspace bootstrap; load with -p tests.use_ops_support."""

import httpx
import pytest
from sqlalchemy import text

from tests.helpers.workspace import TEST_DB


async def bind_fixture_members(session):
    """Mirror the migration's existing-user mapping after test users/roles have been seeded."""
    assert session.get_bind().engine.url.database == TEST_DB
    await session.execute(
        text("""
        INSERT INTO ontology_member (ontology_id,user_id,role)
        SELECT 1,u.id,CASE WHEN EXISTS (
            SELECT 1 FROM sys_role_member m JOIN sys_role r ON r.id=m.role_id
            WHERE m.user_id=u.id AND r.func->>'releases'='manage') THEN 'owner'
        WHEN EXISTS (SELECT 1 FROM sys_role_member m JOIN sys_role r ON r.id=m.role_id,
            jsonb_each_text(r.func) f WHERE m.user_id=u.id AND f.value='manage') THEN 'editor'
        ELSE 'viewer' END FROM sys_user u
        ON CONFLICT (ontology_id,user_id) DO UPDATE SET role=excluded.role
    """)
    )
    await session.execute(
        text("""
        INSERT INTO ontology_role_binding (ontology_id,role_id) SELECT 1,id FROM sys_role
        ON CONFLICT (ontology_id,role_id) DO NOTHING
    """)
    )


async def grant_ops_roles(session, assignments=None):
    from tests.function_access_support import grant_fixture_roles

    await grant_fixture_roles(session, assignments)
    await bind_fixture_members(session)


@pytest.fixture(autouse=True)
async def _ops_workspace(session):
    from app.common.ontology import set_ontology_scope
    from app.seeds.loader import run_seeds

    assert session.get_bind().engine.url.database == TEST_DB
    await set_ontology_scope(session, 1)
    await run_seeds(session, ["admin"])
    await bind_fixture_members(session)


@pytest.fixture(autouse=True)
def _no_external_http(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("External HTTP is forbidden in ops tests; use ASGITransport/MockTransport")

    async def denied_async(*args, **kwargs):
        denied()

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", denied_async)
