"""对象历史只读真实 CR 关联，历史上下文不混入后续发布和当前草稿。"""

from sqlalchemy import select

from app.core import clock
from app.modules.static.models import ChangeRequest, CrItem, CRLevel, CRStatus, CrTarget
from app.modules.static.sections import BUILDERS
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.sections import isolated_sections
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1"


async def test_object_history_saved_rows_and_release_boundary(client, session):
    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
    await grant_fixture_roles(session)
    release = await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == "v2026.10.3"))

    def record(code, status, release_id=None):
        row = ChangeRequest(
            code=code,
            title="真实历史检查 " + code,
            owner="实际作者",
            reviewer="实际审核人",
            status=status,
            level=CRLevel.minor,
            status_at=clock.now(),
            release_id=release_id,
            changes=1,
            detail={},
        )
        session.add(row)
        return row

    draft = record("CR-9901", CRStatus.draft)
    published = record("CR-9902", CRStatus.published, release.id)
    unrelated = record("CR-9903", CRStatus.draft)
    relation = record("CR-9904", CRStatus.draft)
    await session.flush()
    session.add_all(
        [
            CrTarget(cr_id=draft.id, target_kind="object", target_id="store"),
            CrItem(
                cr_id=draft.id,
                target_kind="object",
                target_id="store",
                section="properties",
                item_key="name",
                op="update",
                before={"name": "门店"},
                after={"name": "门店新名称"},
            ),
            CrTarget(cr_id=published.id, target_kind="object", target_id="store"),
            CrTarget(cr_id=unrelated.id, target_kind="object", target_id="product"),
            CrItem(
                cr_id=relation.id,
                target_kind="link",
                target_id="history_relation",
                section="def",
                item_key="from",
                op="update",
                before="store",
                after="product",
            ),
        ]
    )
    await session.flush()
    path = P + "/object-types/store/usage"
    response = await client.get(path, params={"kind": "cr", "q": "真实历史检查"})
    assert response.status_code == 200, response.text
    rows = {r["cr"]["id"]: r for r in response.json()["items"]}
    assert set(rows) == {draft.code, published.code, relation.code}
    assert rows[draft.code]["parts"] == ["对象类型", "属性"]
    assert rows[published.code]["cr"]["release"] == release.ver
    assert rows[relation.code]["parts"] == ["关系"]
    response = await client.get(path, params={"kind": "cr", "q": "真实历史检查", "ver": "v2026.10.3"})
    assert response.status_code == 200, response.text
    assert [r["cr"]["id"] for r in response.json()["items"]] == [published.code]
    response = await client.get(path, params={"kind": "cr", "q": "真实历史检查", "ver": "v2026.10.2"})
    assert response.status_code == 200 and response.json()["items"] == []
    response = await client.get(path, params={"kind": "cr", "q": "真实历史检查", "filter": "草稿"})
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 2
