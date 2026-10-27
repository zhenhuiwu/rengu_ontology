"""admin_0026: deleting a role removes its ontology bindings; service-role use blocks with 409."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import set_ontology_scope
from app.modules.admin.models import SysRole
from app.modules.ontology.models import OntologyRoleBinding, OntologyWorkspace

ADMIN = {"X-Demo-As": "U010245"}
URL = "/api/admin/v1/roles"


async def in_ontology(conn, ontology_id, fn):
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as s:
        await set_ontology_scope(s, ontology_id)
        result = await fn(s)
        await s.commit()  # releases the savepoint into the test transaction
        return result


def count_bindings(role_id, ontology_id):
    return lambda s: s.scalar(
        select(func.count())
        .select_from(OntologyRoleBinding)
        .where(OntologyRoleBinding.role_id == role_id, OntologyRoleBinding.ontology_id == ontology_id)
    )


def bind(ontology_id, role_id):
    async def go(s):
        s.add(OntologyRoleBinding(ontology_id=ontology_id, role_id=role_id))

    return go


async def test_delete_bound_role_cascades_bindings_in_every_ontology(client, seeded, conn):
    created = await client.post(URL, json={"name": "绑定待删"}, headers=ADMIN)
    assert created.status_code == 200, created.text
    role_id = await seeded.scalar(select(SysRole.id).where(SysRole.code == created.json()["id"]))
    other = OntologyWorkspace(code="role-del-second", name="role delete second")
    seeded.add(other)
    await seeded.flush()
    await in_ontology(conn, 1, bind(1, role_id))
    await in_ontology(conn, other.id, bind(other.id, role_id))
    assert await in_ontology(conn, other.id, count_bindings(role_id, other.id)) == 1

    response = await client.delete(f"{URL}/{created.json()['id']}", headers=ADMIN)
    assert response.status_code == 204, response.text  # was 500 (FK violation)
    assert await in_ontology(conn, 1, count_bindings(role_id, 1)) == 0
    # Invisible to the request's ontology scope, still removed by the FK cascade.
    assert await in_ontology(conn, other.id, count_bindings(role_id, other.id)) == 0


async def test_delete_service_role_is_role_in_use(client, seeded):
    created = await client.post(URL, json={"name": "服务角色待删"}, headers=ADMIN)
    code = created.json()["id"]
    sa = await client.post(
        "/api/admin/v1/service-accounts", json={"name": "svc-del", "roleId": code}, headers=ADMIN
    )
    assert sa.status_code == 201, sa.text
    response = await client.delete(f"{URL}/{code}", headers=ADMIN)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "ROLE_IN_USE"
    assert body["details"] == {"count": 1, "serviceAccounts": [{"id": sa.json()["id"], "name": "svc-del"}]}
    assert (await client.get(f"{URL}/{code}", headers=ADMIN)).status_code == 200
