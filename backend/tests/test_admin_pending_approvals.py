"""Real pending counts never use user-code hashes or silently transfer approvals."""

import pytest
from sqlalchemy import select

from app.core import clock
from app.modules.admin.models import SysUser, UserStatus
from app.modules.admin.service import pending_approvals
from app.modules.dynamic.models import ActionRun, ActionStatus, SourceType
from app.modules.static.models import ChangeRequest, CRLevel, CRStatus
from tests.admin_committed_support import committed_approval_rows  # noqa: F401
from tests.function_access_support import grant_fixture_roles


@pytest.fixture
async def approvals(users, committed_approval_rows):  # noqa: F811
    await grant_fixture_roles(users)
    a, b, zero, duplicate = list(
        await users.scalars(
            select(SysUser)
            .where(SysUser.role.is_not(None), SysUser.status == UserStatus.active)
            .order_by(SysUser.code)
            .limit(4)
        )
    )
    a.name, b.name, duplicate.name = "审批甲唯一", "审批乙同名", "审批乙同名"
    a.status = b.status = zero.status = duplicate.status = UserStatus.active

    def cr(code, name, reviewer_code=None, status=CRStatus.in_review):
        return ChangeRequest(
            code=code,
            title=code,
            owner="创建者",
            reviewer=name,
            status=status,
            level=CRLevel.minor,
            changes=1,
            detail={"reviewerCode": reviewer_code} if reviewer_code is not None else {},
            status_at=clock.now(),
            created_by=zero.code,
        )

    records = [
        cr("CR-PENDING-A", a.name, a.code),
        cr("CR-PENDING-B", b.name, b.code),
        cr("CR-PENDING-LEGACY", a.name),
        cr("CR-PENDING-AMBIGUOUS", b.name),
        cr("CR-PENDING-UNKNOWN", a.name, "UNKNOWN"),
        cr("CR-PENDING-DRAFT", a.name, a.code, CRStatus.draft),
    ]
    for index, approver in enumerate((a.code, b.code, a.name, "UNKNOWN")):
        records.append(
            ActionRun(
                code=f"ACT-PENDING-{index}",
                action_type_code="fixture_action",
                action_name="实际待审批动作",
                source_type=SourceType.manual,
                source_label="手动",
                initiator=zero.code,
                approver=approver,
                idem_key=f"pending-key-{index}",
                status=ActionStatus.approving,
                submitted_at=clock.now(),
            )
        )
    await committed_approval_rows(*records)
    await users.flush()
    return users, a, b, zero


async def test_real_batch_counts_unique_legacy_names_and_unknowns(client, approvals):
    session, a, b, zero = approvals
    counts = await pending_approvals(session, [a, b, zero])
    assert counts == {a.code: 3, b.code: 2, zero.code: 0}
    page = await client.get("/api/admin/v1/users", params={"kw": "审批", "size": 10})
    assert page.status_code == 200, page.text
    rows = {row["id"]: row for row in page.json()["items"]}
    assert rows[a.code]["pendingApprovals"] == 3 and rows[b.code]["pendingApprovals"] == 2
    zero_page = (await client.get("/api/admin/v1/users", params={"kw": zero.code})).json()
    assert zero_page["items"][0]["pendingApprovals"] == 0


async def test_disable_with_pending_rejects_fake_transfer_without_mutation(client, approvals):
    session, _a, b, zero = approvals
    response = await client.post(f"/api/admin/v1/users/{b.code}:disable", json={})
    assert response.status_code == 422 and response.json()["code"] == "TRANSFER_REQUIRED"
    response = await client.post(f"/api/admin/v1/users/{b.code}:disable", json={"transferTo": zero.code})
    assert response.status_code == 409 and response.json()["code"] == "TRANSFER_UNAVAILABLE"
    assert b.status == UserStatus.active
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == "CR-PENDING-B"))
    assert cr.detail["reviewerCode"] == b.code
    action = await session.scalar(select(ActionRun).where(ActionRun.code == "ACT-PENDING-1"))
    assert action.approver == b.code
    response = await client.post(f"/api/admin/v1/users/{zero.code}:disable", json={})
    assert response.status_code == 200 and response.json()["pendingApprovals"] == 0
