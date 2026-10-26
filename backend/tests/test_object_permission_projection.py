"""对象权限投影只做SQL角色分页，不取用户或成员明细。"""

import pytest
from sqlalchemy import event, select

from app.common.pagination import PageParams
from app.core import clock
from app.modules.admin.models import SysRole, SysRoleScope
from app.modules.admin.roles import RoleService
from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectStatus, ObjectType
from tests.function_access_support import grant_fixture_roles

URL = "/api/admin/v1/roles/object-permissions"
HEADERS = {"X-Demo-As": "U010001"}


@pytest.fixture
async def projected(users):
    await grant_fixture_roles(users)
    users.add(
        ObjectType(
            code="permission_obj",
            api_name="PermissionObject",
            display_name="权限真对象",
            obj_group="测试",
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=Lifecycle.active,
            status=ObjectStatus.published,
        )
    )
    for i in range(23):
        role = SysRole(
            code=f"RP-{i:02}",
            name=f"权限投影角色{i}",
            func={},
            objs=[
                {
                    "objType": "旧对象标签",
                    "api": "PermissionObject",
                    "view": True,
                    "export": False,
                    "actions": [],
                },
                {"objType": "权限真对象", "api": "WrongApi", "view": True, "export": True, "actions": []},
            ],
            default_deny=True,
            changed_at=clock.now(),
            changed_by="测试",
        )
        users.add(role)
        await users.flush()
        if i == 0:
            for j, label in enumerate(["旧对象标签", "PermissionObject", "unrelated"]):
                users.add(
                    SysRoleScope(
                        code=f"S-P-{j}",
                        role_id=role.id,
                        obj_type=label,
                        prop="大区",
                        op="=",
                        values=["华东"],
                        seq=j,
                    )
                )
    await users.flush()
    return users


async def test_projection_pages_and_no_user_payload(client, projected):
    first = await client.get(URL, params={"objectId": "permission_obj"}, headers=HEADERS)
    assert first.status_code == 200, first.text
    first = first.json()
    second = (await client.get(URL, params={"objectId": "permission_obj", "page": 2}, headers=HEADERS)).json()
    assert first["size"] == 10 and len(first["items"]) == len(second["items"]) == 10
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    last = (await client.get(URL, params={"objectId": "permission_obj", "page": 999}, headers=HEADERS)).json()
    assert last["page"] == (last["total"] + 9) // 10 and last["items"]
    rows = (await client.get(URL, params={"objectId": "permission_obj", "size": 200}, headers=HEADERS)).json()
    row = next(r for r in rows["items"] if r["id"] == "RP-00")
    assert [p["api"] for p in row["objs"]] == ["PermissionObject"]
    assert {s["objType"] for s in row["scopes"]} == {"旧对象标签", "PermissionObject"}
    assert all(
        set(r) == {"id", "name", "defaultDeny", "objs", "scopes", "memberCount"} for r in rows["items"]
    )
    assert first["object"] == {"id": "permission_obj", "name": "权限真对象", "api": "PermissionObject"}


async def test_service_sql_bounded_without_users(projected):
    queries = []
    connection = await projected.connection()

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        queries.append(statement.lower())

    event.listen(connection.sync_connection, "before_cursor_execute", record)
    try:
        await RoleService(projected).object_permissions("permission_obj", PageParams(page=2, size=10))
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record)
    role_reads = [q for q in queries if "from sys_role " in q and "count(" not in q]
    assert role_reads and all(" limit " in q and " offset " in q for q in role_reads)
    assert not any("sys_user" in q for q in queries)
    assert not any("from property" in q for q in queries)


async def test_projection_identity_status_and_permission(client, projected):
    for missing in ["order", "PermissionObject", "权限真对象"]:
        response = await client.get(URL, params={"objectId": missing}, headers=HEADERS)
        assert response.status_code == 404, response.text
    assert (await client.get(URL, params={"objectId": "bad\x00id"}, headers=HEADERS)).status_code == 422
    obj = await projected.scalar(select(ObjectType).where(ObjectType.code == "permission_obj"))
    obj.status = ObjectStatus.draft
    await projected.flush()
    assert (await client.get(URL, params={"objectId": "permission_obj"}, headers=HEADERS)).status_code == 404
    role = await projected.scalar(select(SysRole).where(SysRole.code == "R-01"))
    original = dict(role.func)
    for permission in ("users", "roles", "object-types"):
        role.func = {**original, permission: "none"}
        await projected.flush()
        assert (
            await client.get(URL, params={"objectId": "permission_obj"}, headers=HEADERS)
        ).status_code == 403
