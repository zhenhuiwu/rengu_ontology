"""Canonical object PK flags are synchronized only through publication, scoped to one object."""

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import set_ontology_scope
from app.modules.static.models import (
    ChangeRequest,
    ConflictStrategy,
    CRStatus,
    Lifecycle,
    ObjectType,
    Property,
)
from app.modules.versioning.models import OntologyRelease
from tests import test_static_build_writes as build_support
from tests import test_static_writeback as support

seeded = support.seeded
ws2 = build_support.ws2


async def test_pk_change_overrides_stale_property_flags_and_other_scope_is_untouched(ws2, conn):
    wid, client = ws2
    assert conn.engine.url.database == os.environ.get("RENGU_TEST_DB")
    assert conn.engine.url.database.startswith(("rengu_test", "rengu_fix_"))
    code = "FIX_PK_CANONICAL"

    def obj():
        return ObjectType(
            code=code,
            api_name="PkCanonical",
            display_name="主键标记核验",
            obj_group="测试",
            description="测试定义",
            pk_property="code",
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=Lifecycle.active,
            properties=[
                Property(
                    api_name="code",
                    display_name="业务编号",
                    data_type="string",
                    prop_group="标识",
                    source_column="code",
                    description="",
                    is_pk=True,
                ),
                Property(
                    api_name="id",
                    display_name="物理ID",
                    data_type="int",
                    prop_group="标识",
                    source_column="id",
                    description="",
                    is_pk=False,
                ),
            ],
        )

    for oid in (1, wid):
        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as scoped:
            await set_ontology_scope(scoped, oid)
            scoped.add(obj())
            if oid == 1:
                for cr in await scoped.scalars(
                    select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
                ):
                    cr.status = CRStatus.draft
            await scoped.commit()
    target = {"kind": "object", "id": code, "name": "主键标记核验"}
    for section in ("def", "properties"):
        response = await client.post(
            support.P + "/change-requests",
            headers=support.LIU,
            json={"title": "主键标记-" + section, "reviewer": "U010001", "target": target},
        )
        assert response.status_code == 201, response.text
        cr = response.json()["id"]
        rows = (
            [{"key": "pk", "op": "update", "before": "code", "after": "id"}]
            if section == "def"
            else [
                {"key": "code", "op": "update", "after": {"pk": True}},
                {"key": "id", "op": "update", "after": {"pk": False}},
            ]
        )
        await support.put(client, cr, target, section, rows)
        for action, headers in (("submit", support.LIU), ("approve", support.WANG)):
            response = await client.post(
                support.P + f"/change-requests/{cr}:{action}", headers=headers, json={}
            )
            assert response.status_code == 200, response.text
        response = await client.post(support.P + "/releases", headers=support.WANG, json={})
        assert response.status_code == 201, response.text
        for oid in (1, wid):
            async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as scoped:
                await set_ontology_scope(scoped, oid)
                current = await scoped.scalar(select(ObjectType).where(ObjectType.code == code))
                assert current.pk_property == ("id" if oid == 1 else "code")
                assert {p.api_name: p.is_pk for p in current.properties} == (
                    {"code": False, "id": True} if oid == 1 else {"code": True, "id": False}
                )
                if oid == 1:
                    release = await scoped.scalar(
                        select(OntologyRelease).where(OntologyRelease.ver == response.json()["ver"])
                    )
                    snap = release.snapshot["properties"][code]
                    assert not snap["code"].get("pk") and snap["id"]["pk"] is True
