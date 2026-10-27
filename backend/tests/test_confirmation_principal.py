"""A confirmed action keeps its original criteria principal after account changes."""

from types import SimpleNamespace

import pytest

from app.common.auth import resolve_user
from app.core import clock
from app.core.errors import Forbidden, Unauthorized
from app.modules.admin.models import SysUser, UserStatus
from app.modules.integration.execution import criteria_user
from app.modules.ontology.models import OntologyMember


@pytest.fixture
async def principals(session):
    assert session.get_bind().engine.url.database == "rengu_fix_admin_20261027"
    approver = SysUser(code="FIX-CRITERIA-APPROVER", name="测试审批人", status=UserStatus.active)
    confirmer = SysUser(code="FIX-CRITERIA-CONFIRMER", name="测试确认人", status=UserStatus.active)
    session.add_all([approver, confirmer])
    await session.flush()
    memberships = [
        OntologyMember(ontology_id=1, user_id=user.id, role="editor") for user in (approver, confirmer)
    ]
    session.add_all(memberships)
    await session.flush()
    return session, await resolve_user(session, approver.code), confirmer, memberships[1]


async def test_valid_confirmer_remains_criteria_principal(principals):
    session, approver, confirmer, _ = principals
    result = await criteria_user(
        session, approver, SimpleNamespace(confirmer=confirmer.code, confirmed_at=clock.now())
    )
    assert result.id == confirmer.code and result.id != approver.id


@pytest.mark.parametrize("revoked", ["disabled", "membership"])
async def test_revoked_confirmer_never_falls_back_to_approver(principals, revoked):
    session, approver, confirmer, membership = principals
    if revoked == "disabled":
        confirmer.status = UserStatus.disabled
        expected = Unauthorized
    else:
        await session.delete(membership)
        expected = Forbidden
    await session.flush()
    with pytest.raises(expected) as error:
        await criteria_user(
            session, approver, SimpleNamespace(confirmer=confirmer.code, confirmed_at=clock.now())
        )
    assert error.value.code == expected.code


@pytest.mark.parametrize("confirmed", [True, False])
async def test_self_and_unconfirmed_keep_current_principal(principals, confirmed):
    session, approver, confirmer, _ = principals
    run = SimpleNamespace(
        confirmer=approver.id if confirmed else confirmer.code,
        confirmed_at=clock.now() if confirmed else None,
    )
    assert await criteria_user(session, approver, run) is approver
