"""QA-only real workspace fixtures; do not bypass identity, row filters, or RLS."""

import pytest
from sqlalchemy import select

from app.common.ontology import set_ontology_scope
from app.modules.admin.models import SysRole, SysUser
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding


@pytest.fixture(autouse=True)
async def qa_workspace(session):
    from app.seeds.loader import run_seeds

    users = session
    await set_ontology_scope(users, 1)
    await run_seeds(users, ["admin"])
    members = set(await users.scalars(select(OntologyMember.user_id).where(OntologyMember.ontology_id == 1)))
    bindings = set(
        await users.scalars(select(OntologyRoleBinding.role_id).where(OntologyRoleBinding.ontology_id == 1))
    )
    users.add_all(
        [
            OntologyMember(ontology_id=1, user_id=pk, role="editor")
            for pk in await users.scalars(select(SysUser.id))
            if pk not in members
        ]
    )
    users.add_all(
        [
            OntologyRoleBinding(ontology_id=1, role_id=pk)
            for pk in await users.scalars(select(SysRole.id))
            if pk not in bindings
        ]
    )
    await users.flush()
    return users


@pytest.fixture
async def users(qa_workspace):
    return qa_workspace


@pytest.fixture
async def committed_qa(engine):
    """Only the named test DB: seed committed source rows visible to the real independent SELECT connection."""
    from sqlalchemy import func
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.modules.biz.models import Store
    from app.seeds.loader import run_seeds
    from tests.qa_access_support import seed_qa_access

    assert engine.url.database == "rengu_test_use_qa_20261027"
    local = create_async_engine(engine.url, poolclass=NullPool)
    try:
        async with AsyncSession(local, expire_on_commit=False) as setup:
            await set_ontology_scope(setup, 1)
            if not await setup.scalar(select(func.count()).select_from(Store)):
                await run_seeds(setup, ["admin"])
                await qa_workspace.__wrapped__(setup)
                await seed_qa_access(setup)
                await setup.commit()
    finally:
        await local.dispose()
