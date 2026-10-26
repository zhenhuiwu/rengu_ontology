"""Dimension matching uses persisted definitions/one CR target, never writes or invents hit counts."""

import json
from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.core import clock
from app.modules.admin.models import SysRole, SysUser
from app.modules.static.models import (
    ConflictStrategy,
    CrItem,
    DefStatus,
    Dimension,
    DimKind,
    DimMapping,
    DimValue,
    DimValueStatus,
    Lifecycle,
    MappingSource,
    MatchMode,
    ObjectType,
)
from app.modules.static.service import StaticService
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.service import snapshot_digest
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1"
ADMIN = {"X-Demo-As": "U010001"}
VIEWER = {"X-Demo-As": "U010006"}


@pytest.fixture
async def actors(users):
    await grant_fixture_roles(users)
    role = await users.scalar(select(SysRole).where(SysRole.code == "R-03"))
    role.func = {"dimensions": "view", "releases": "view"}
    await grant_fixture_roles(users, {"U010006": "R-03"})
    await users.flush()
    return users


@pytest.fixture
async def facts(actors):
    session = actors
    obj = ObjectType(
        code="trial_object",
        api_name="TrialObject",
        display_name="真实测试对象",
        obj_group="测试",
        conflict_strategy=ConflictStrategy.user_edit_wins,
        lifecycle=Lifecycle.active,
        properties=[],
    )
    session.add(obj)
    await session.flush()
    dims = []
    for index, code in enumerate(("trial_a", "trial_b")):
        dimension = Dimension(
            code=code,
            api_name=code,
            display_name=f"数据库维度{index}",
            kind=DimKind.dimension,
            dim_group="测试",
            status=DefStatus.published,
            owner="真实测试用户",
            object=obj,
            object_name=obj.display_name,
            attr="",
            source_column="",
            value_src="",
            sync="",
            description="",
            qa={},
            history=[],
            since_release="v2026.01.1",
            levels=[],
        )
        session.add(dimension)
        await session.flush()
        session.add(
            DimValue(
                dimension_id=dimension.id,
                value_key="city:SH",
                code="SH",
                name="上海",
                level_name="city",
                status=DimValueStatus.enabled,
                synonyms=["沪"],
            )
        )
        dims.append(dimension)
    session.add_all(
        [
            DimValue(
                dimension_id=dims[0].id,
                value_key="city:SZ",
                code="SZ",
                name="深圳",
                level_name="city",
                status=DimValueStatus.disabled,
                synonyms=["深"],
            ),
            DimMapping(
                dimension_id=dims[0].id,
                map_key="east",
                phrase="东部",
                targets=["上海"],
                match=MatchMode.exact,
                source=MappingSource.knowledge,
                hits=17,
                resolve="使用上海",
            ),
            DimMapping(
                dimension_id=dims[0].id,
                map_key="south",
                phrase="南方",
                targets=["深圳", "不存在的取值"],
                match=MatchMode.multi,
                source=MappingSource.knowledge,
                hits=3,
            ),
        ]
    )
    for index in range(7):
        session.add(
            DimValue(
                dimension_id=dims[0].id,
                value_key=f"east:{index}",
                code=f"E{index}",
                name=f"华东{index}",
                level_name="city",
                status=DimValueStatus.enabled,
                synonyms=[],
            )
        )
    await session.flush()
    return session


async def match(client, text, **body):
    return await client.post(
        f"{P}/dimensions:match", headers=ADMIN, json={"text": text, "dimension": "trial_a", **body}
    )


async def test_database_matching_scope_contains_and_real_hits(client, facts):
    before = await facts.scalar(select(func.count()).select_from(DimValue))
    response = await match(client, "沪", all=True)
    assert response.status_code == 200, response.text
    data = response.json()
    assert {hit["item"] for hit in data["exact"]} == {"trial_a", "trial_b"}
    assert data["crossDim"] and len(data["groups"]) == 2
    data = (await match(client, "华东")).json()
    assert len(data["contains"]) == 6 and data["more"] == 1
    data = (await match(client, "东部")).json()
    assert data["exact"][0]["value"] == "上海" and data["rule"]["hits"] == 17
    assert (await match(client, "南方")).json()["exact"] == []
    assert (await match(client, "模型不能猜一个值")).json()["exact"] == []
    rows = (await client.get(f"{P}/dimensions/trial_a/mappings?size=200", headers=ADMIN)).json()["items"]
    assert next(row for row in rows if row["phrase"] == "沪")["hits"] is None
    assert next(row for row in rows if row["phrase"] == "东部")["hits"] == 17
    assert await facts.scalar(select(func.count()).select_from(DimValue)) == before


async def test_historical_snapshot_and_preview_are_read_only(client, facts):
    from app.common.context import ViewContext

    definition = deepcopy(
        next(d for d in await StaticService(facts, ViewContext())._dims() if d["id"] == "trial_a")
    )
    definition["name"] = "历史数据库维度"
    definition["values"] = [
        {"key": "old", "code": "OLD", "name": "旧值", "level": "city", "synonyms": ["旧词"], "status": "启用"}
    ]
    definition["mappings"] = []
    snapshot = {"dimensions": {"trial_a": definition}}
    owner = await facts.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    facts.add(
        OntologyRelease(
            ver="v2026.01.1",
            snapshot=snapshot,
            snapshot_sha256=snapshot_digest(snapshot),
            published_by_id=owner,
            published_at=clock.now(),
        )
    )
    await facts.flush()
    response = await client.post(
        f"{P}/dimensions:match?ver=v2026.01.1&previewAs=U010006",
        headers=ADMIN,
        json={"text": "旧词", "dimension": "trial_a"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["exact"] == [{"item": "trial_a", "itemName": "历史数据库维度", "value": "旧值"}]
    assert (
        await client.post(
            f"{P}/dimensions:match?ver=v2026.01.1", headers=ADMIN, json={"text": "沪", "dimension": "trial_a"}
        )
    ).json()["exact"] == []
    assert (await match(client, "沪")).json()["exact"][0]["value"] == "上海"


async def test_only_selected_saved_cr_target_is_overlaid_and_permissions_hold(client, facts):
    target = {"kind": "dimension", "id": "trial_a"}
    response = await client.post(
        f"{P}/change-requests", headers=ADMIN, json={"title": "真实试算草稿", "target": target}
    )
    assert response.status_code == 201, response.text
    cr = response.json()["id"]
    assert (
        await client.post(
            f"{P}/change-requests/{cr}/targets", headers=ADMIN, json={"kind": "dimension", "id": "trial_b"}
        )
    ).status_code == 200
    for code in ("trial_a", "trial_b"):
        response = await client.put(
            f"{P}/change-requests/{cr}/items",
            headers=ADMIN,
            json={
                "target": {"kind": "dimension", "id": code},
                "section": "values",
                "rows": [
                    {
                        "key": "new",
                        "op": "add",
                        "after": {
                            "code": "NEW",
                            "name": f"仅草稿{code}",
                            "level": "city",
                            "status": "启用",
                            "synonyms": [],
                        },
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
    before = await facts.scalar(select(func.count()).select_from(CrItem))
    assert (await match(client, "仅草稿trial_a")).json()["exact"] == []
    response = await match(client, "仅草稿trial_a", crId=cr, all=True)
    assert response.status_code == 200, response.text
    assert response.json()["exact"][0]["item"] == "trial_a"
    assert (await match(client, "仅草稿trial_b", crId=cr, all=True)).json()["exact"] == []
    response = await client.post(
        f"{P}/dimensions:match", headers=VIEWER, json={"text": "沪", "dimension": "trial_a", "crId": cr}
    )
    assert response.status_code == 403 and response.json()["code"] == "NO_PERMISSION"
    response = await client.post(
        f"{P}/dimensions:match?previewAs=U010006",
        headers=ADMIN,
        json={"text": "沪", "dimension": "trial_a", "crId": cr},
    )
    assert response.status_code == 403
    response = await client.post(
        f"{P}/dimensions:match?ver=v2026.01.1",
        headers=ADMIN,
        json={"text": "沪", "dimension": "trial_a", "crId": cr},
    )
    assert response.status_code == 422
    assert await facts.scalar(select(func.count()).select_from(CrItem)) == before
    assert (await match(client, "仅草稿trial_a")).json()["exact"] == []


@pytest.mark.parametrize(
    "body,status,code",
    [
        ({}, 409, "EMPTY_INPUT"),
        ({"text": " \n "}, 409, "EMPTY_INPUT"),
        ({"text": "x\x00", "all": True}, 422, "VALIDATION_FAILED"),
        ({"text": "\ud800", "all": True}, 422, "VALIDATION_FAILED"),
        ({"text": "x" * 513, "all": True}, 422, "VALIDATION_FAILED"),
        ({"text": "x", "dimension": "a\x00"}, 422, "VALIDATION_FAILED"),
        ({"text": "x", "dimension": "\ud800"}, 422, "VALIDATION_FAILED"),
        ({"text": "x", "dimension": "a", "crId": "bad\x00"}, 422, "VALIDATION_FAILED"),
    ],
)
async def test_input_boundaries(client, actors, body, status, code):
    response = await client.post(
        f"{P}/dimensions:match",
        headers={**ADMIN, "Content-Type": "application/json"},
        content=json.dumps(body, ensure_ascii=True).encode(),
    )
    assert response.status_code == status, response.text
    assert response.json()["code"] == code
