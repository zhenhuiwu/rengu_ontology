"""构建写入闭环：两个本体工作空间各自经 CR 新建同编码对象 → 发布 → 整版回滚，互不影响（ontology_id 隔离）。

数据只用种子 + 本测试事务内新建，不改导出的演示数据。会话不能切换本体，空间 2 的每个请求用新 Session。
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import set_ontology_scope
from app.modules.admin.models import SysRole, SysUser
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding, OntologyWorkspace
from app.modules.static.models import ObjectType
from tests.test_static_writeback import LIU, WANG, P, seeded  # noqa: F401  (fixture 复用)


def obj(code="bw_obj"):
    return {"kind": "object", "id": code, "name": "构建测试对象", "isNew": True}


async def put(client, cr, section, rows, headers, code):
    r = await client.put(
        f"{P}/change-requests/{cr}/items",
        json={"target": obj(code), "section": section, "rows": rows},
        headers=headers,
    )
    assert r.status_code == 200, r.text


async def publish_new_object(client, liu, wang, desc, code="bw_obj") -> tuple[str, str]:
    """草稿 → 提交 → 审核 → 发布；返回 (CR 编号, 版本号)。"""
    r = await client.post(
        f"{P}/change-requests", json={"title": f"构建写入-{desc}", "target": obj(code)}, headers=liu
    )
    assert r.status_code == 201, r.text
    cr, v0 = r.json()["id"], r.json()["lockVersion"]
    await put(client, cr, "def", [
        {"key": "name", "op": "add", "after": "构建测试对象"},
        {"key": "group", "op": "add", "after": "业务"},
        {"key": "desc", "op": "add", "after": desc},
    ], liu, code)  # fmt: skip
    pk = {"api": "id", "name": "编号", "type": "string", "group": "标识", "desc": "主键", "pk": True}
    await put(client, cr, "properties", [{"key": "id", "op": "add", "after": pk}], liu, code)
    assert (await client.get(f"{P}/change-requests/{cr}", headers=wang)).json()["lockVersion"] == v0 + 2
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=liu)
    assert r.status_code == 200, r.text
    r = await client.post(f"{P}/change-requests/{cr}:approve", headers=wang)
    assert r.status_code == 200, r.text
    r = await client.post(f"{P}/releases", json={}, headers=wang)
    assert r.status_code == 201 and not r.json().get("skipped"), r.text
    return cr, r.json()["ver"]


@pytest.fixture
async def ws2(seeded, conn):  # noqa: F811
    """空间 2：王敏 owner、刘婷 editor，绑定种子全部系统角色；请求级新 Session。"""
    from app.db.session import get_session
    from app.main import app

    ws = OntologyWorkspace(code="static-bw-2", name="静态构建测试空间", description="", domain="tests")
    seeded.add(ws)
    await seeded.flush()
    for code, role in (("U010001", "owner"), ("U010004", "editor")):
        uid = await seeded.scalar(select(SysUser.id).where(SysUser.code == code))
        seeded.add(OntologyMember(ontology_id=ws.id, user_id=uid, role=role))
    for rid in await seeded.scalars(select(SysRole.id)):
        seeded.add(OntologyRoleBinding(ontology_id=ws.id, role_id=rid))
    await seeded.commit()
    previous = app.dependency_overrides.get(get_session)

    async def fresh():
        # 同 get_session：成功提交（落到外层测试事务的 SAVEPOINT），出错回滚。
        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_session] = fresh
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield ws.id, c
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


async def scoped_obj(conn, ontology_id):
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as s:
        await set_ontology_scope(s, ontology_id)
        o = await s.scalar(select(ObjectType).where(ObjectType.code == "bw_obj"))
        return None if o is None else o.description


async def test_second_workspace_publish_rollback_isolated(ws2, conn):
    wid, c = ws2
    h2 = {"X-Ontology-Id": str(wid)}
    liu2, wang2 = {**LIU, **h2}, {**WANG, **h2}
    cur1 = (await c.get(f"{P}/releases/current", headers=WANG)).json()
    # 新空间首版没有上一版、不可回滚（versioning/service.py:89 latest.parent 为空），先发一版基线
    await publish_new_object(c, liu2, wang2, "空间2基线", code="bw_base")
    cur1 = (await c.get(f"{P}/releases/current", headers=WANG)).json()

    cr2, ver2 = await publish_new_object(c, liu2, wang2, "空间2")
    assert (await c.get(f"{P}/object-types/bw_obj", headers=wang2)).status_code == 200
    # 空间 1 看不到空间 2 的定义、单子和版本
    assert (await c.get(f"{P}/object-types/bw_obj", headers=WANG)).status_code == 404
    # 单号按空间各自编号：空间 1 同号单子（若有）是种子单，不是空间 2 的这张
    r = await c.get(f"{P}/change-requests/{cr2}", headers=WANG)
    assert r.status_code == 404 or r.json()["title"] != "构建写入-空间2", r.text
    assert (await c.get(f"{P}/releases/current", headers=WANG)).json() == cur1

    # 空间 1 同编码发布：独立存在，不撞唯一键
    _, ver1 = await publish_new_object(c, LIU, WANG, "空间1")
    assert (await scoped_obj(conn, 1), await scoped_obj(conn, wid)) == ("空间1", "空间2")

    # 空间 2 回滚只撤空间 2 的新对象
    r = await c.post(f"{P}/releases/{ver2}:rollback", json={}, headers=wang2)
    assert r.status_code == 201, r.text
    assert (await c.get(f"{P}/object-types/bw_obj", headers=wang2)).status_code == 404
    assert (await c.get(f"{P}/object-types/bw_obj", headers=WANG)).status_code == 200
    assert (await scoped_obj(conn, 1), await scoped_obj(conn, wid)) == ("空间1", None)
    assert (await c.get(f"{P}/object-types/bw_base", headers=wang2)).status_code == 200

    # 再回滚空间 1：空间 2 不受影响
    r = await c.post(f"{P}/releases/{ver1}:rollback", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    assert await scoped_obj(conn, 1) is None
