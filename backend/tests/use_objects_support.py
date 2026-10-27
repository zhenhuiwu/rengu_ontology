"""Opt-in object tests: real workspace scope/membership, confined to the one object test database."""

import pytest
from sqlalchemy import select

from app.common.ontology import set_ontology_scope
from app.modules.admin.models import SysRole, SysUser
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding
from tests.helpers.workspace import TEST_DB


async def bind_objects_workspace(session):
    assert session.get_bind().engine.url.database == TEST_DB
    session.get_bind().engine.hide_parameters = True
    members = set(
        await session.scalars(select(OntologyMember.user_id).where(OntologyMember.ontology_id == 1))
    )
    bindings = set(
        await session.scalars(select(OntologyRoleBinding.role_id).where(OntologyRoleBinding.ontology_id == 1))
    )
    for uid, code in await session.execute(select(SysUser.id, SysUser.code)):
        if uid not in members:
            session.add(
                OntologyMember(ontology_id=1, user_id=uid, role="owner" if code == "U010001" else "editor")
            )
    for rid in await session.scalars(select(SysRole.id)):
        if rid not in bindings:
            session.add(OntologyRoleBinding(ontology_id=1, role_id=rid))
    await session.flush()


async def enable_fixture_export(session):
    """Explicit export capability for export assertions, never an implicit admin bypass."""
    assert session.get_bind().engine.url.database == TEST_DB
    role = await session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    role.objs = [{**item, "export": item.get("view") is True} for item in role.objs]
    await session.flush()


@pytest.fixture(autouse=True)
async def objects_scope(session):
    assert session.get_bind().engine.url.database == TEST_DB
    session.get_bind().engine.hide_parameters = True
    await set_ontology_scope(session, 1)


@pytest.fixture(autouse=True)
async def objects_workspace(objects_scope, users):
    await bind_objects_workspace(users)
