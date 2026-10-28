"""实际DB角色、HTTP叶级/整单权限与发布服务防绕过；没有测试免检身份。"""

import pytest
from sqlalchemy import delete, func, select

from app.common.auth import CurrentUser, check_func, resolve_user
from app.core import clock
from app.core.errors import Forbidden
from app.modules.admin.models import SysRole, SysRoleMember, SysUser, UserStatus
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding
from app.modules.static.models import ChangeRequest, CrCheck, CrEvent, CrTarget
from app.modules.static.permissions import TARGET_PAGE
from app.modules.static.publish import PublishService
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1"


@pytest.fixture
async def authorized(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await grant_fixture_roles(session, {"U010001": "R-01"})
        yield session


async def actor(session, tag, permissions):
    role = SysRole(
        code=f"R-A-{tag}",
        name=f"权限角色{tag}",
        func=permissions,
        objs=[],
        changed_at=clock.now(),
        changed_by="权限专项",
    )
    user = SysUser(code=f"U-A-{tag}", name=f"权限用户{tag}", status=UserStatus.active)
    session.add_all([role, user])
    await session.flush()
    session.add_all(
        [
            SysRoleMember(role_id=role.id, user_id=user.id, seq=0),
            OntologyMember(
                ontology_id=1,
                user_id=user.id,
                role="editor" if "manage" in permissions.values() else "viewer",
            ),
            OntologyRoleBinding(ontology_id=1, role_id=role.id),
        ]
    )
    await session.flush()
    return {"X-Demo-As": user.code}, user, role


async def test_db_role_union_and_revocation_without_config_bypass(authorized):
    session = authorized
    _, user, role = await actor(session, "union", {"object-types": "view", "metrics": "view"})
    higher = SysRole(
        code="R-A-union2",
        name="较高权限",
        func={"object-types": "manage", "metrics": "none", "releases": "invalid"},
        objs=[],
        changed_at=clock.now(),
        changed_by="权限专项",
    )
    session.add(higher)
    await session.flush()
    session.add_all(
        [
            SysRoleMember(role_id=higher.id, user_id=user.id, seq=0),
            OntologyRoleBinding(ontology_id=1, role_id=higher.id),
        ]
    )
    await session.flush()
    resolved = await resolve_user(session, user.code)
    assert resolved.func == {"object-types": "manage", "metrics": "view"}
    assert resolved.roles == (role.code, higher.code)
    assert resolved.ontology_role == "viewer" and not resolved.is_ontology_admin
    check_func(resolved, "object-types", "manage")
    with pytest.raises(Forbidden):
        check_func(resolved, "releases", "manage")
    await session.execute(delete(SysRoleMember).where(SysRoleMember.user_id == user.id))
    assert (await resolve_user(session, user.code)).func == {}
    admin = await session.scalar(select(SysUser).where(SysUser.code == "U010001"))
    before = await resolve_user(session, admin.code)
    assert before.is_ontology_admin and before.func["releases"] == "manage"
    await session.execute(delete(SysRoleMember).where(SysRoleMember.user_id == admin.id))
    resolved = await resolve_user(session, admin.code)
    assert resolved.roles == () and resolved.func == {}
    assert resolved.is_ontology_admin == (resolved.func.get("releases") == "manage")
    assert not resolved.is_ontology_admin
    with pytest.raises(Forbidden):
        check_func(resolved, "releases", "manage")


@pytest.mark.parametrize(
    "kind", ["object", "link", "metric", "dimension", "knowledge", "action", "event", "rule"]
)
async def test_target_editor_needs_target_manage_not_release_manage(client, authorized, kind):
    page = TARGET_PAGE[kind]
    headers, _, role = await actor(authorized, kind, {page: "manage", "releases": "view"})
    target = {"kind": kind, "id": f"auth-{kind}", "isNew": True}
    response = await client.post(
        P + "/change-requests", json={"title": "权限专项", "target": target}, headers=headers
    )
    assert response.status_code == 201, response.text
    code = response.json()["id"]
    rows = [{"key": "dedupWindow", "op": "update", "after": "24 小时"}] if kind == "event" else []
    put = {"target": target, "section": "def", "rows": rows}
    response = await client.put(f"{P}/change-requests/{code}/items", json=put, headers=headers)
    assert response.status_code == 200, response.text
    role.func = {page: "view", "releases": "view"}
    await authorized.flush()
    before = await authorized.scalar(
        select(CrTarget.id).join(ChangeRequest).where(ChangeRequest.code == code)
    )
    denied = await client.put(f"{P}/change-requests/{code}/items", json=put, headers=headers)
    assert denied.status_code == 403 and denied.json()["details"]["page"] == page
    denied = await client.delete(f"{P}/change-requests/{code}/targets/{kind}/{target['id']}", headers=headers)
    assert denied.status_code == 403
    assert await authorized.scalar(select(CrTarget.id).where(CrTarget.id == before)) == before


async def test_release_manager_cannot_edit_other_target_pages(client, authorized):
    headers, _, _ = await actor(authorized, "release", {"releases": "manage"})
    body = {"title": "禁止越域", "target": {"kind": "object", "id": "auth-object", "isNew": True}}
    before = await authorized.scalar(select(func.count()).select_from(ChangeRequest))
    response = await client.post(P + "/change-requests", json=body, headers=headers)
    assert response.status_code == 403 and response.json()["details"]["page"] == "object-types"
    assert await authorized.scalar(select(func.count()).select_from(ChangeRequest)) == before


async def test_viewer_can_read_but_cannot_write_and_readonly_wins(client, authorized):
    headers, _, _ = await actor(authorized, "view", {"object-types": "view", "releases": "view"})
    assert (await client.get(P + "/object-types", headers=headers)).status_code == 200
    body = {"title": "不能写", "target": {"kind": "object", "id": "auth-view", "isNew": True}}
    assert (await client.post(P + "/change-requests", json=body, headers=headers)).status_code == 403
    for query in ["ver=v2026.10.3", "previewAs=U010001"]:
        response = await client.post(P + "/change-requests?" + query, json=body, headers=headers)
        assert response.status_code == 403 and response.json()["code"] == "READONLY"


@pytest.mark.parametrize("legacy", [False, True])
async def test_whole_cr_operations_cover_mixed_and_legacy_targets(client, authorized, legacy):
    headers, user, _ = await actor(
        authorized, f"mixed{legacy}", {"object-types": "manage", "releases": "view"}
    )
    created = await client.post(
        P + "/change-requests",
        json={"title": "混合目标", "target": {"kind": "object", "id": f"auth-mixed{legacy}", "isNew": True}},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    code = created.json()["id"]
    cr = await authorized.scalar(select(ChangeRequest).where(ChangeRequest.code == code))
    if legacy:
        cr.detail = dict(cr.detail) | {"touches": ["property:store.format", "metric:legacy"]}
    else:
        authorized.add(
            CrTarget(
                cr_id=cr.id,
                target_kind="metric",
                target_id="auth-metric",
                is_new=True,
                seq=1,
                created_by=user.code,
            )
        )
    await authorized.flush()
    before = (cr.title, cr.lock_version)
    events = await authorized.scalar(select(func.count()).select_from(CrEvent))
    for method, path, body in [
        ("PATCH", "", {"title": "越域修改"}),
        ("POST", ":validate", None),
        ("POST", ":submit", None),
        ("DELETE", "", None),
    ]:
        response = await client.request(
            method, f"{P}/change-requests/{code}{path}", json=body, headers=headers
        )
        assert response.status_code == 403, response.text
        assert response.json()["details"]["page"] == "metrics"
    await authorized.refresh(cr)
    assert (cr.title, cr.lock_version) == before
    assert await authorized.scalar(select(func.count()).select_from(CrEvent)) == events
    assert (
        await authorized.scalar(select(func.count()).select_from(CrCheck).where(CrCheck.cr_id == cr.id)) == 0
    )


async def test_publish_and_rollback_services_require_release_manage(authorized):
    user = CurrentUser(id="auth-direct", name="直接调用", is_ontology_admin=True, func={"releases": "view"})
    service = PublishService(authorized)
    for call in [service.publish(user, None), service.rollback(user, "v2026.10.3", None)]:
        with pytest.raises(Forbidden) as error:
            await call
        assert error.value.details == {"page": "releases", "required": "manage"}


async def test_unknown_user_has_no_role_bypass_and_unknown_identity_is_401(client, authorized):
    headers, _, _ = await actor(authorized, "empty", {})
    response = await client.get(P + "/object-types", headers=headers)
    assert response.status_code == 403
    assert (await client.get(P + "/object-types", headers={"X-Demo-As": "NO-SUCH-USER"})).status_code == 401


async def test_build_owner_directory_is_db_active_and_target_authorized(client, authorized):
    headers, user, role = await actor(authorized, "owners", {"object-types": "manage"})
    authorized.add_all(
        [
            SysUser(code="U-A-disabled", name="停用负责人", status=UserStatus.disabled),
            SysUser(code="U-A-pending", name="待激活负责人", status=UserStatus.pending),
        ]
    )
    await authorized.flush()
    for kind in ["object", "metric", "dimension", "action", "event"]:
        role.func = {TARGET_PAGE[kind]: "manage"}
        await authorized.flush()
        response = await client.get(P + "/build/owners", params={"kind": kind}, headers=headers)
        assert response.status_code == 200, response.text
        rows = response.json()
        expected = (
            await authorized.execute(
                select(SysUser.code, SysUser.name)
                .where(SysUser.status == UserStatus.active)
                .order_by(SysUser.code)
            )
        ).all()
        assert rows == [{"id": code, "name": name} for code, name in expected]
        assert all(set(row) == {"id", "name"} for row in rows)
        assert {"U-A-disabled", "U-A-pending"}.isdisjoint(row["id"] for row in rows)
        role.func = {TARGET_PAGE[kind]: "view"}
        await authorized.flush()
        assert (
            await client.get(P + "/build/owners", params={"kind": kind}, headers=headers)
        ).status_code == 403
    current = await client.get("/api/admin/v1/me/row-scope", headers=headers)
    assert current.status_code == 200 and current.json()["userName"] == user.name
    assert (
        await client.get(P + "/build/owners", params={"kind": "arbitrary"}, headers=headers)
    ).status_code == 422
