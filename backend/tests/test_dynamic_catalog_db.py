"""Request catalogs use current typed definitions and actual workspace members."""

from sqlalchemy import func, select

from app.common.auth import resolve_user
from app.main import app
from app.modules.admin.models import SysUser, UserStatus
from app.modules.dynamic.catalog import get_catalog
from app.modules.dynamic.models import ActionRun
from app.modules.dynamic.ports import UnconfiguredAdapters, get_checker
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from tests.test_use_ops_runtime import API, payload, runtime  # noqa: F401


async def test_catalog_same_name_uses_stable_type_id_and_real_system_shape(runtime):  # noqa: F811
    session, first, second = runtime
    user = await resolve_user(session, "U010001")
    first.name = second.name = "重名动作"
    first.approval, second.approval = "U010001", "OPS-LIMIT"
    system = ExtSystem(
        code="OPS-CATALOG", name="测试系统目录", timeout_ms=1000, status=ExtSystemStatus.ok, env="测试"
    )
    session.add(system)
    await session.flush()
    catalog = await get_catalog(session, user)
    assert catalog.action_type_by_name("重名动作") is None and catalog.action_meta("重名动作") is None
    assert catalog.action_meta(first.code)["approverId"] == "U010001"
    assert catalog.action_meta(second.code)["approverId"] == "OPS-LIMIT"
    assert catalog.ext_system(system.code)["env"] == "测试"
    assert all(set(row) == {"id", "name"} for row in catalog.meta()["actionSystems"])
    second.name = "实时改名"
    await session.flush()
    assert (await get_catalog(session, user)).action_type_by_name("实时改名")["id"] == second.code


async def test_approval_requires_explicit_exemption_or_unique_active_whole_person(runtime):  # noqa: F811
    session, action, _ = runtime
    user = await resolve_user(session, "U010001")
    for value, configured, required in (
        ("", False, True),
        ("经理审批", False, True),
        ("王敏慧 审批", False, True),
        ("免审", True, False),
        ("免审（需经理审批）", False, True),
        ("王敏 审批", True, True),
    ):
        action.approval = value
        await session.flush()
        meta = (await get_catalog(session, user)).action_meta(action.code)
        assert (meta["approvalConfigured"], meta["approvalRequired"]) == (configured, required)
    other = await session.scalar(select(SysUser).where(SysUser.code == "OPS-LIMIT"))
    other.name, action.approval = "王敏", "王敏"
    await session.flush()
    assert (await get_catalog(session, user)).action_meta(action.code)["approvalConfigured"] is False
    action.approval = "U010001"
    await session.flush()
    assert (await get_catalog(session, user)).action_meta(action.code)["approverId"] == "U010001"
    person = await session.scalar(select(SysUser).where(SysUser.code == "U010001"))
    person.status = UserStatus.disabled
    await session.flush()
    assert (await get_catalog(session, user)).action_meta(action.code)["approvalConfigured"] is False


async def test_default_missing_checker_does_not_save_successful_action(runtime, client):  # noqa: F811
    session = runtime[0]
    app.dependency_overrides[get_checker] = lambda: UnconfiguredAdapters()
    before = await session.scalar(select(func.count()).select_from(ActionRun))
    response = await client.post(f"{API}/action-runs:stage", json=payload())
    assert response.status_code == 503 and response.json()["code"] == "ACTION_CHECKER_UNAVAILABLE", (
        response.text
    )
    assert await session.scalar(select(func.count()).select_from(ActionRun)) == before


async def test_request_cannot_override_published_approver(runtime, client):  # noqa: F811
    session = runtime[0]
    before = await session.scalar(select(func.count()).select_from(ActionRun))
    response = await client.post(f"{API}/action-runs:stage", json={**payload(), "approver": "OPS-LIMIT"})
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_FAILED", response.text
    assert "不能由请求覆盖" in response.text
    assert await session.scalar(select(func.count()).select_from(ActionRun)) == before
