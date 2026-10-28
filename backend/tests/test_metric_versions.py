"""指标真实版本/基线、回滚CR、发布原子性与整版回滚后账本恢复。"""

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from app.modules.static.metric_versions import FIELDS, definition
from app.modules.static.models import ChangeRequest, CrItem, CRStatus, Metric, ObjectType
from app.modules.versioning.models import OntologyRelease
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles
from tests.helpers.workspace import bind_workspace_members

P = "/api/ontology/v1"
AUTHOR = {"X-Demo-As": "U010004"}
ADMIN = {"X-Demo-As": "U010001"}


@pytest.fixture
async def mv(session):
    from app.modules.biz import governance  # noqa: F401  Register the real built-in order mapping.
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await grant_fixture_roles(session)
        await bind_workspace_members(session)
        # Leave the seeded metric and its demo execution semantics unchanged.
        seed = await session.scalar(select(Metric).where(Metric.code == "gmv"))
        order = await session.scalar(select(ObjectType).where(ObjectType.code == "order"))
        assert seed is not None and order is not None
        session.add(
            Metric(
                code="metric_version_fixture",
                api_name="MetricVersionFixture",
                display_name="版本专项指标",
                metric_group="测试",
                unit=seed.unit,
                owner=seed.owner,
                status=seed.status,
                version=seed.version,
                version_rows=deepcopy(seed.version_rows),
                formula="COUNT(order)",
                dims=deepcopy(seed.dims),
                def_rows=[{"k": "时间字段", "v": "order.created_at"}],
            )
        )
        await session.flush()
        # 本专项只发布自己创建的定义变更，既有已通过单子保持原记录但不混入本次。
        for row in await session.scalars(
            select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
        ):
            row.status = CRStatus.draft
        await session.commit()
        yield session


async def metric(session, code="metric_version_fixture"):
    return await session.scalar(
        select(Metric).where(Metric.code == code).execution_options(populate_existing=True)
    )


async def create_cr(client, code="metric_version_fixture", new=False):
    r = await client.post(
        P + "/change-requests",
        json={
            "title": "指标版本专项",
            "reason": "真实口径变更说明",
            "reviewer": "王敏",
            "target": {"kind": "metric", "id": code, "name": "真实指标", "isNew": new},
        },
        headers=AUTHOR,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def edit(client, values, code="metric_version_fixture", new=False):
    if new:
        values = {"timeField": "order.created_at", **values}
    cr = await create_cr(client, code, new)
    r = await client.put(
        P + f"/change-requests/{cr}/items",
        json={
            "target": {"kind": "metric", "id": code, "isNew": new},
            "section": "def",
            "rows": [
                {"key": key, "op": "add" if new else "update", "after": value}
                for key, value in values.items()
            ],
        },
        headers=AUTHOR,
    )
    assert r.status_code == 200, r.text
    return cr


async def approve(client, cr):
    validation = await client.post(P + f"/change-requests/{cr}:validate", headers=AUTHOR)
    assert validation.status_code == 200, validation.text
    checks = (await client.get(P + f"/change-requests/{cr}", headers=AUTHOR)).json()["detail"]["checks"]
    assert next(row for row in checks if row["code"] == "V5")["result"] != "阻断", checks
    r = await client.post(P + f"/change-requests/{cr}:submit", headers=AUTHOR)
    assert r.status_code == 200, r.text
    r = await client.post(P + f"/change-requests/{cr}:approve", headers=ADMIN)
    assert r.status_code == 200, r.text


async def publish(client, cr):
    await approve(client, cr)
    r = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert r.status_code == 201, r.text
    assert r.json()["skipped"] == []
    return r.json()


async def state(session):
    m = await metric(session)
    return (
        definition(m),
        m.version,
        deepcopy(m.version_rows),
        await session.scalar(select(func.count()).select_from(OntologyRelease)),
        await session.scalar(select(func.count()).select_from(ChangeRequest)),
    )


async def test_actual_versions_baseline_and_rollback_only_stages_cr(client, mv):
    m = await metric(mv)
    original, previous, legacy = definition(m), m.version, deepcopy(m.version_rows)
    target = await mv.scalar(select(Metric).where(Metric.code != m.code, Metric.status == m.status).limit(1))
    variants = [{"code": target.code, "label": "真实变体", "default": True}]
    first = await publish(
        client,
        await edit(
            client,
            {"formula": "COUNT(order) * 2", "unit": "元", "dims": ["门店"], "variants": variants},
        ),
    )
    m = await metric(mv)
    v1 = m.version
    assert int(v1[1:]) > int(previous[1:])
    assert all(row in m.version_rows for row in legacy)
    baseline = next(row for row in m.version_rows if row.get("baseline"))
    assert baseline["definition"] == original and baseline["ver"] == previous
    assert baseline["release"] == "" and "非原发布时间" in baseline["change"]
    assert baseline["at"] == m.version_rows[0]["at"]
    assert m.version_rows[0]["release"] == first["ver"] and m.version_rows[0]["by"] == "王敏"
    assert set(m.version_rows[0]["definition"]) == set(FIELDS) | {"execDef"}
    assert m.version_rows[0]["definition"]["execDef"] == original["execDef"]
    assert m.version_rows[0]["definition"]["variants"] == variants
    await publish(
        client, await edit(client, {"formula": "COUNT(order) * 3", "dims": ["区域"], "variants": []})
    )
    m = await metric(mv)
    v2 = m.version
    assert int(v2[1:]) > int(v1[1:])
    rows = (
        await client.get(P + "/metrics/metric_version_fixture/versions", params={"size": 200}, headers=AUTHOR)
    ).json()["items"]
    assert len({row["ver"] for row in rows}) == len(rows)
    assert next(row for row in rows if row["ver"] == previous)["canRollback"] is True
    before = await state(mv)
    rollback = await client.post(
        P + "/metrics/metric_version_fixture:rollback",
        json={"toVersion": v1, "reason": "恢复真实已存口径", "reviewer": "王敏"},
        headers=AUTHOR,
    )
    assert rollback.status_code == 200, rollback.text
    assert await state(mv) == (*before[:-1], before[-1] + 1)  # 只新增CR，当前定义与发布版本完全不动。
    diff = {row["key"]: row["after"] for row in rollback.json()["items"]}
    assert diff["formula"] == "COUNT(order) * 2" and diff["dims"] == ["门店"] and diff["variants"] == variants
    assert int(diff["version"][1:]) > int(v2[1:])
    await publish(client, rollback.json()["crId"])
    current = await metric(mv)
    assert current.formula == diff["formula"] and current.dims == diff["dims"]
    assert current.version == diff["version"]
    assert current.version_rows[0]["change"] == "恢复真实已存口径"
    assert len([row for row in current.version_rows if row.get("definition") and row["ver"] == v1]) == 1


async def test_legacy_metadata_is_not_a_rollback_definition(client, mv):
    before = await state(mv)
    versions = (await client.get(P + "/metrics/metric_version_fixture/versions", headers=AUTHOR)).json()[
        "items"
    ]
    old = next(row for row in versions if row["ver"] != before[1])
    assert old["canRollback"] is False and old["rollbackUnavailable"] == "未保存该版本定义"
    r = await client.post(
        P + "/metrics/metric_version_fixture:rollback", json={"toVersion": old["ver"]}, headers=AUTHOR
    )
    assert r.status_code == 409 and r.json()["code"] == "METRIC_VERSION_UNAVAILABLE"
    assert await state(mv) == before


async def test_whole_rollback_restores_null_formula_and_real_dims(client, mv):
    first = await edit(client, {"formula": None, "dims": ["门店"]})
    validated = await client.post(P + f"/change-requests/{first}:validate", headers=AUTHOR)
    assert validated.status_code == 200, validated.text
    checks = (await client.get(P + f"/change-requests/{first}", headers=AUTHOR)).json()["detail"]["checks"]
    v5 = next(row for row in checks if row["code"] == "V5")
    assert v5["result"] == "警告" and "没有可执行的指标公式" in v5["note"]
    await publish(client, first)
    prior = await state(mv)
    second = await publish(client, await edit(client, {"formula": "COUNT(order) * 4", "dims": ["区域"]}))
    response = await client.post(P + f"/releases/{second['ver']}:rollback", json={}, headers=ADMIN)
    assert response.status_code == 201, response.text
    restored = await metric(mv)
    assert definition(restored) == prior[0] and restored.version == prior[1]
    assert {row["ver"] for row in restored.version_rows if row.get("definition")} > {
        row["ver"] for row in prior[2] if row.get("definition")
    }


@pytest.mark.parametrize("invalid", ["v1", "version-long-invalid", ["v9"], 4, None])
async def test_invalid_or_reused_version_publish_rolls_back(client, mv, invalid):
    original = await state(mv)
    cr = await edit(client, {"formula": "COUNT(order) * 5", "version": invalid})
    await approve(client, cr)
    r = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["code"] == "WRITEBACK_FAILED", r.text
    after = await state(mv)
    assert after[:4] == original[:4]
    check = await mv.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))
    assert check.status == CRStatus.approved


@pytest.mark.parametrize("values", [{"name": "x" * 65}, {"dims": [1]}, {"api": "OrderCount"}])
async def test_invalid_definition_publish_is_atomic(client, mv, values):
    if "api" in values:
        values = {
            "api": await mv.scalar(
                select(Metric.api_name).where(Metric.code != "metric_version_fixture").limit(1)
            )
        }
    original = await state(mv)
    cr = await edit(client, {"formula": "COUNT(order) * 6", **values})
    await approve(client, cr)
    response = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert response.status_code == 409 and response.json()["code"] == "WRITEBACK_FAILED", response.text
    assert (await state(mv))[:4] == original[:4]


async def test_ambiguous_real_history_rejects_without_creating_cr(client, mv):
    m = await metric(mv)
    snapshot = definition(m)
    m.version_rows = [
        {"ver": "v1", "definition": snapshot, "release": "real-one"},
        {"ver": "v1", "definition": {**snapshot, "formula": "另一个真实定义"}, "release": "real-two"},
    ]
    await mv.commit()
    original = await state(mv)
    versions = (await client.get(P + "/metrics/metric_version_fixture/versions", headers=AUTHOR)).json()[
        "items"
    ]
    assert next(row for row in versions if row["ver"] == "v1")["canRollback"] is False
    response = await client.post(
        P + "/metrics/metric_version_fixture:rollback", json={"toVersion": "v1"}, headers=AUTHOR
    )
    assert response.status_code == 409 and response.json()["code"] == "METRIC_VERSION_AMBIGUOUS"
    assert await state(mv) == original


async def test_join_lock_and_target_edit_conflict_keep_existing_draft(client, mv):
    previous = (await metric(mv)).version
    await publish(client, await edit(client, {"formula": "COUNT(order) * 7"}))
    cr = await create_cr(client)
    head = (await client.get(P + f"/change-requests/{cr}", headers=AUTHOR)).json()
    wrong = await client.post(
        P + "/metrics/metric_version_fixture:rollback",
        json={"toVersion": previous, "crId": cr, "lockVersion": head["lockVersion"] + 1},
        headers=AUTHOR,
    )
    assert wrong.status_code == 409 and wrong.json()["code"] == "VERSION_CONFLICT"
    empty = await client.post(
        P + "/metrics/metric_version_fixture:rollback",
        json={"toVersion": previous, "crId": cr},
        headers=AUTHOR,
    )
    assert empty.status_code == 422
    good = await client.post(
        P + "/metrics/metric_version_fixture:rollback",
        json={"toVersion": previous, "crId": cr, "lockVersion": head["lockVersion"]},
        headers=AUTHOR,
    )
    assert good.status_code == 200, good.text
    saved_head = (await client.get(P + f"/change-requests/{cr}", headers=AUTHOR)).json()
    saved_rows = list(
        await mv.scalars(
            select(CrItem.after)
            .join(ChangeRequest, ChangeRequest.id == CrItem.cr_id)
            .where(ChangeRequest.code == cr)
        )
    )
    again = await client.post(
        P + "/metrics/metric_version_fixture:rollback",
        json={"toVersion": previous, "crId": cr, "lockVersion": saved_head["lockVersion"]},
        headers=AUTHOR,
    )
    assert again.status_code == 409 and again.json()["code"] == "METRIC_DRAFT_NOT_EMPTY"
    assert (await client.get(P + f"/change-requests/{cr}", headers=AUTHOR)).json()[
        "lockVersion"
    ] == saved_head["lockVersion"]
    assert (
        list(
            await mv.scalars(
                select(CrItem.after)
                .join(ChangeRequest, ChangeRequest.id == CrItem.cr_id)
                .where(ChangeRequest.code == cr)
            )
        )
        == saved_rows
    )


async def test_whole_release_rollback_keeps_real_ledger_for_recreated_metric(client, mv):
    code = "metric_history_new"
    first = await publish(
        client,
        await edit(
            client,
            {
                "name": "真实新指标",
                "api": "MetricHistoryNew",
                "formula": "COUNT(order) * 8",
                "version": "v1",
                "unit": "元",
            },
            code,
            True,
        ),
    )
    reverted = await client.post(P + f"/releases/{first['ver']}:rollback", json={}, headers=ADMIN)
    assert reverted.status_code == 201, reverted.text
    assert await metric(mv, code) is None  # 账本不能破坏“新指标回滚后消失”。
    await publish(
        client,
        await edit(
            client,
            {
                "name": "重建指标",
                "api": "MetricHistoryNew",
                "formula": "COUNT(order) * 9",
                "version": "v2",
                "unit": "元",
            },
            code,
            True,
        ),
    )
    restored = await metric(mv, code)
    actual = [row for row in restored.version_rows if row.get("definition")]
    assert {row["ver"] for row in actual} == {"v1", "v2"}
    assert next(row for row in actual if row["ver"] == "v1")["definition"]["formula"] == "COUNT(order) * 8"
    planned = await client.post(
        P + f"/metrics/{code}:rollback", json={"toVersion": "v1", "reviewer": "王敏"}, headers=AUTHOR
    )
    assert planned.status_code == 200, planned.text
    assert (
        next(row for row in planned.json()["items"] if row["key"] == "formula")["after"] == "COUNT(order) * 8"
    )


async def test_permissions_readonly_and_nul_path(client, mv):
    for query in ({"ver": "v2026.10.1"}, {"previewAs": "U010002"}):
        response = await client.post(
            P + "/metrics/metric_version_fixture:rollback",
            params=query,
            json={"toVersion": "v1"},
            headers=AUTHOR,
        )
        assert response.status_code == 403 and response.json()["code"] == "READONLY"
    invalid = await client.post(P + "/metrics/bad%00id:rollback", json={"toVersion": "v1"}, headers=AUTHOR)
    assert invalid.status_code == 422, invalid.text
    from app.modules.admin.models import SysRole

    role = await mv.scalar(select(SysRole).where(SysRole.code == "R-01"))
    role.func = {**role.func, "metrics": "view"}
    await mv.flush()
    denied = await client.post(
        P + "/metrics/metric_version_fixture:rollback", json={"toVersion": "v1"}, headers=ADMIN
    )
    assert denied.status_code == 403 and denied.json()["code"] == "NO_PERMISSION"
