"""关系/知识取号与登记同事务，并发等待真实咨询锁。"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.ontology import set_ontology_scope
from app.modules.static.cr_write import CrWriteService
from app.modules.static.models import ChangeRequest, CrTarget
from app.modules.static.schemas import CRCreateIn
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles
from tests.test_creation_code_lock import _two_creates

P = "/api/ontology/v1"


@pytest.mark.parametrize("kind,prefix", [("link", "L-"), ("knowledge", "K-")])
async def test_target_allocation_serializes_through_registration(engine, kind, prefix):
    actor = CurrentUser(
        id="allocate-test",
        name=uuid4().hex,
        func={"releases": "manage", "link-types": "manage", "glossary": "manage"},
    )
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        await set_ontology_scope(setup, 1)
        crs = [
            await CrWriteService(setup).create(actor, CRCreateIn(title=f"分配{n}", reviewer=""))
            for n in (1, 2)
        ]
        await setup.commit()

    async def allocate(session, n):
        return (await CrWriteService(session).allocate_target(crs[n - 1], actor, kind, "真实定义")).target_id

    try:
        first, second = await _two_creates(engine, allocate)
        assert first.startswith(prefix) and int(second[len(prefix) :]) == int(first[len(prefix) :]) + 1
        async with engine.connect() as check:
            ids = select(ChangeRequest.id).where(ChangeRequest.code.in_(crs))
            assert set(await check.scalars(select(CrTarget.target_id).where(CrTarget.cr_id.in_(ids)))) == {
                first,
                second,
            }
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(delete(ChangeRequest).where(ChangeRequest.code.in_(crs)))


async def test_allocation_http_permission_readonly_and_text(client, session):
    await run_seeds(session, ["admin"])
    await grant_fixture_roles(session)
    created = await client.post(P + "/change-requests", json={"title": "后端分配"})
    assert created.status_code == 201, created.text
    cr = created.json()["id"]
    body = {"kind": "knowledge", "name": "实际知识"}
    response = await client.post(f"{P}/change-requests/{cr}/targets:allocate", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["target"]["isNew"] is True
    assert response.json()["target"]["id"].startswith("K-")
    for query in ("?ver=v2026.10.1", "?previewAs=U010004"):
        denied = await client.post(f"{P}/change-requests/{cr}/targets:allocate{query}", json=body)
        assert denied.status_code == 403 and denied.json()["code"] == "READONLY"
    denied = await client.post(
        f"{P}/change-requests/{cr}/targets:allocate", json=body, headers={"X-Demo-As": "U010006"}
    )
    assert denied.status_code == 403
    for name in ("bad\x00name", "bad\ud800name"):
        invalid = await client.post(
            f"{P}/change-requests/{cr}/targets:allocate",
            content=json.dumps({**body, "name": name}).encode(),
            headers={"Content-Type": "application/json"},
        )
        assert invalid.status_code == 422
