"""Quality CRUD drafts publish atomically; execution/history never use pending definitions."""

import pytest
from sqlalchemy import func, select

from app.modules.admin.models import SysRole
from app.modules.data.quality_models import QualityCheck, QualityRun
from app.modules.data.sections import BUILDERS
from app.modules.static.models import ChangeRequest, CRStatus
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.sections import register_section
from tests.test_data_quality import quality_db, static_sections, static_seeded  # noqa: F401

P = "/api/ontology/v1"
Q = "/api/data/v1/quality-checks"
OWNER = {"X-Demo-As": "U010004"}
ADMIN = {"X-Demo-As": "U010001"}
BODY = {
    "name": "数据库检查",
    "typeKey": "order",
    "kind": "唯一性",
    "threshold": "重复 0 条",
    "schedule": "手动",
}


@pytest.fixture(autouse=True)
async def author_scope(quality_db):  # noqa: F811
    from tests.function_access_support import grant_fixture_roles

    await grant_fixture_roles(quality_db, {"U010004": "R-02"})
    role = await quality_db.scalar(select(SysRole).where(SysRole.code == "R-02"))
    role.func = {**role.func, "data-quality": "manage"}
    await quality_db.flush()


async def publish(client, cr):
    response = await client.post(f"{P}/change-requests/{cr}:submit", headers=OWNER)
    assert response.status_code == 200, response.text
    response = await client.post(f"{P}/change-requests/{cr}:approve", headers=ADMIN)
    assert response.status_code == 200, response.text
    response = await client.post(f"{P}/releases", headers=ADMIN, json={})
    assert response.status_code == 201, response.text
    return response.json()["ver"]


async def test_crud_publish_execution_history_and_rollback(client, quality_db):  # noqa: F811
    register_section(BUILDERS[0])
    # Exclude seeded approved CRs so the release contains precisely this real definition change.
    for cr in await quality_db.scalars(select(ChangeRequest)):
        cr.status = CRStatus.published
    response = await client.post(Q, json=BODY, headers=OWNER)
    assert response.status_code == 201, response.text
    new = response.json()
    assert await quality_db.scalar(select(func.count()).select_from(QualityCheck)) == 0
    draft = (await client.get(Q, headers=OWNER)).json()["items"][0]
    assert draft["hasPublished"] is False and draft["pending"]["definition"]["name"] == BODY["name"]
    assert (await client.post(f"{Q}/{new['id']}:run", headers=OWNER)).status_code == 404
    first_ver = await publish(client, new["crId"])
    live = await quality_db.scalar(select(QualityCheck))
    assert live.name == BODY["name"] and live.created_by == "U010004"
    run = await client.post(f"{Q}/{new['id']}:run", headers=OWNER)
    assert run.status_code == 200, run.text
    assert run.json()["run"]["definition"]["threshold"] == "重复 0 条"
    response = await client.put(f"{Q}/{new['id']}", headers=OWNER, json={**BODY, "threshold": "重复 9 条"})
    assert response.status_code == 200, response.text
    edit = response.json()
    assert live.threshold == "重复 0 条"
    assert (await client.get(Q, headers=OWNER)).json()["items"][0]["pending"]["definition"][
        "threshold"
    ] == "重复 9 条"
    assert (await client.get(Q, headers=ADMIN)).json()["items"] == []
    stale = await client.put(
        f"{Q}/{new['id']}", headers=OWNER, json={**BODY, "crId": edit["crId"], "lockVersion": 1}
    )
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    await publish(client, edit["crId"])
    assert live.threshold == "重复 9 条"
    response = await client.request("DELETE", f"{Q}/{new['id']}", headers=OWNER, json={})
    assert response.status_code == 200, response.text
    delete = response.json()
    assert live.retired_at is None
    delete_ver = await publish(client, delete["crId"])
    assert live.retired_at is not None
    assert (await client.post(f"{Q}/{new['id']}:run", headers=OWNER)).status_code == 409
    assert (await client.post(Q + ":run-all", headers=OWNER)).status_code == 409
    history = (await client.get(f"{Q}/{new['id']}/runs", headers=OWNER)).json()
    assert history["total"] == 1 and history["items"][0]["definition"]["threshold"] == "重复 0 条"
    assert (await client.get(Q, headers=OWNER)).json()["summary"]["total"] == 0
    historical = (await client.get(Q, headers=OWNER, params={"ver": first_ver})).json()["items"][0]
    assert (
        historical["threshold"] == "重复 0 条" and not historical["retired"] and historical["pending"] is None
    )
    rollback = await client.post(f"{P}/releases/{delete_ver}:rollback", headers=ADMIN, json={})
    assert rollback.status_code == 201, rollback.text
    assert live.retired_at is None and live.threshold == "重复 9 条"
    assert await quality_db.scalar(select(func.count()).select_from(QualityRun)) == 1
    assert await quality_db.scalar(select(func.count()).select_from(OntologyRelease)) == 9


async def test_permission_readonly_pending_lock_and_owner(client, quality_db):  # noqa: F811
    response = await client.post(Q, headers=OWNER, json=BODY)
    assert response.status_code == 201, response.text
    new = response.json()
    for suffix in ("?ver=v2026.10.3", "?previewAs=U010001"):
        assert (await client.put(f"{Q}/{new['id']}{suffix}", headers=OWNER, json=BODY)).status_code == 403
    other = await client.put(
        f"{Q}/{new['id']}",
        headers=ADMIN,
        json={**BODY, "crId": new["crId"], "lockVersion": new["lockVersion"]},
    )
    assert other.status_code == 404
    missing = await client.put(f"{Q}/{new['id']}", headers=OWNER, json={**BODY, "crId": new["crId"]})
    assert missing.status_code == 409 and missing.json()["code"] == "VERSION_CONFLICT"
    updated = await client.put(
        f"{Q}/{new['id']}",
        headers=OWNER,
        json={**BODY, "name": "保存后恢复", "crId": new["crId"], "lockVersion": new["lockVersion"]},
    )
    assert updated.status_code == 200, updated.text
    row = (await client.get(Q, headers=OWNER)).json()["items"][0]
    assert row["pending"]["definition"]["name"] == "保存后恢复"
    assert row["pending"]["lockVersion"] == updated.json()["lockVersion"]
    assert await quality_db.scalar(select(func.count()).select_from(QualityCheck)) == 0


async def test_database_pagination_filters_and_persisted_run_pages(client, quality_db):  # noqa: F811
    from datetime import timedelta

    from app.core import clock
    from app.modules.static.models import ObjectType

    obj = await quality_db.scalar(select(ObjectType).where(ObjectType.code == "order"))
    for index in range(23):
        quality_db.add(
            QualityCheck(
                code=f"QC-PAGE-{index:02}",
                object_type_id=obj.id,
                name=f"检查{index}",
                kind="唯一性",
                threshold="重复 0 条",
                limit_value=0,
                schedule="手动",
                created_by="U010004",
            )
        )
    await quality_db.flush()
    check = await quality_db.scalar(select(QualityCheck).where(QualityCheck.code == "QC-PAGE-00"))
    for index in range(31):
        quality_db.add(
            QualityRun(
                check_id=check.id,
                job_id=f"JOB-{index}",
                time=clock.now() - timedelta(minutes=31 - index),
                result="失败" if index == 30 else "通过",
                actual=str(index),
                ms=1,
                data_cutoff=clock.now(),
                definition={"name": "运行时定义"},
                created_by="U010004",
            )
        )
    await quality_db.flush()
    response = await client.get(Q, headers=OWNER, params={"page": 2, "size": 10})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 23 and page["page"] == 2 and len(page["items"]) == 10
    assert page["items"][0]["id"] == "QC-PAGE-10" and page["summary"]["failed"] == 1
    last = (await client.get(Q, headers=OWNER, params={"page": 3, "size": 10})).json()
    assert len(last["items"]) == 3 and last["items"][-1]["id"] == "QC-PAGE-22"
    failed = (await client.get(Q, headers=OWNER, params={"result": "失败", "page": 2, "size": 10})).json()
    assert failed["page"] == 1 and failed["total"] == 1 and failed["items"][0]["id"] == check.code
    history = (
        await client.get(f"{Q}/{check.code}/runs", headers=OWNER, params={"page": 2, "size": 10})
    ).json()
    assert history["total"] == 31 and len(history["items"]) == 10
    assert history["items"][0]["actual"] == "20" and history["items"][-1]["actual"] == "11"
