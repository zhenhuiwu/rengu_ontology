"""请求级目录、真实审批路由与缺适配器的原子失败边界。"""

from sqlalchemy import func, select

from app.modules.action.models import ActionType
from app.modules.admin.models import SysUser, UserStatus
from app.modules.dynamic.catalog import get_catalog
from app.modules.dynamic.models import ActionRun
from tests.dynamic_support import API, dyn, fakes  # noqa: F401


async def test_catalog_same_name_uses_stable_type_id_and_real_system_shape(dyn):  # noqa: F811
    types = list(
        await dyn.scalars(
            select(ActionType).where(ActionType.retired_release_id.is_(None)).order_by(ActionType.id).limit(2)
        )
    )
    a, b = types
    a.name = b.name = "重名动作"
    a.approval, b.approval = "王敏", "陈立军"
    await dyn.flush()
    catalog = await get_catalog(dyn)
    assert catalog.action_type_by_name("重名动作") is None and catalog.action_meta("重名动作") is None
    assert catalog.action_meta(a.code)["approverId"] == "U010001"
    assert catalog.action_meta(b.code)["approverId"] == "U010002"
    assert catalog.meta()["actionMeta"][a.code]["approver"] == "王敏"
    assert catalog.ext_system("EXT-ERP")["env"] in ("生产", "测试")
    assert all(set(system) == {"id", "name"} for system in catalog.meta()["actionSystems"])
    b.name = "实时改名"
    await dyn.flush()
    fresh = await get_catalog(dyn)
    assert fresh.action_type_by_name("实时改名")["id"] == b.code


async def test_approval_requires_explicit_exemption_or_unique_active_whole_person(dyn):  # noqa: F811
    at = await dyn.scalar(select(ActionType).where(ActionType.code == "request_replenish"))
    for value, configured, required in (
        ("", False, True),
        ("经理审批", False, True),
        ("王敏慧 审批", False, True),
        ("免审", True, False),
        ("免审（需经理审批）", False, True),
        ("免审（超过需人工复核）", False, True),
        ("王敏 审批", True, True),
    ):
        at.approval = value
        await dyn.flush()
        meta = (await get_catalog(dyn)).action_meta(at.code)
        assert (meta["approvalConfigured"], meta["approvalRequired"]) == (configured, required), value
    other = await dyn.scalar(select(SysUser).where(SysUser.code == "U010002"))
    other.name = "王敏"
    at.approval = "王敏"
    await dyn.flush()
    assert (await get_catalog(dyn)).action_meta(at.code)["approvalConfigured"] is False
    at.approval = "U010001"
    await dyn.flush()
    assert (await get_catalog(dyn)).action_meta(at.code)["approverId"] == "U010001"
    person = await dyn.scalar(select(SysUser).where(SysUser.code == "U010001"))
    person.status = UserStatus.disabled
    await dyn.flush()
    assert (await get_catalog(dyn)).action_meta(at.code)["approvalConfigured"] is False


async def test_default_missing_checker_does_not_save_successful_action(dyn, client):  # noqa: F811
    from app.main import app
    from app.modules.dynamic.ports import UnconfiguredAdapters, get_checker

    app.dependency_overrides[get_checker] = lambda: UnconfiguredAdapters()
    at = await dyn.scalar(select(ActionType).where(ActionType.code == "request_replenish"))
    before = await dyn.scalar(select(func.count()).select_from(ActionRun))
    at.approval = "王敏"
    await dyn.flush()
    response = await client.post(
        f"{API}/action-runs:stage",
        json={"typeId": at.code, "source": {"type": "手动"}, "objId": "1004512", "objLabel": "真实对象"},
    )
    assert response.status_code == 503 and response.json()["code"] == "ACTION_CHECKER_UNAVAILABLE", (
        response.text
    )
    assert await dyn.scalar(select(func.count()).select_from(ActionRun)) == before


async def test_request_cannot_override_published_approver(dyn, client, fakes):  # noqa: F811
    before = await dyn.scalar(select(func.count()).select_from(ActionRun))
    response = await client.post(
        f"{API}/action-runs:stage",
        json={
            "typeId": "request_replenish",
            "source": {"type": "手动"},
            "approver": "陈立军",
            "objId": "1004512",
            "objLabel": "真实对象",
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_FAILED"
    assert "不能由请求覆盖" in response.text
    assert await dyn.scalar(select(func.count()).select_from(ActionRun)) == before
    assert fakes.submitter.calls == []
