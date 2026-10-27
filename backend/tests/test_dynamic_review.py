"""Confirmation disclosures persist only after real checks, and obey current visibility."""

import json
from copy import deepcopy

from sqlalchemy import func, select

from app.modules.dynamic.models import ActionRun, ActionRunLog
from app.modules.event.models import Event
from app.modules.integration.models import WritebackCall
from tests.dynamic_support import dyn  # noqa: F401
from tests.test_palantir_mainline import PLANNER, STAGE, mainline  # noqa: F401
from tests.test_use_ops_runtime import headers, runtime  # noqa: F401

API = "/api/ontology/v1"


async def test_review_preflight_is_no_write_and_stage_snapshot_survives_reload(mainline, session):  # noqa: F811
    client, hits = mainline
    models = (ActionRun, ActionRunLog, Event, WritebackCall)
    before = [await session.scalar(select(func.count()).select_from(m)) for m in models]
    response = await client.post(f"{API}/actions/request_replenish:validate", headers=PLANNER, json={
        "objKind": STAGE["objKind"], "objId": STAGE["objId"], "params": json.loads(STAGE["params"]),
    })
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["humanChecks"] and review["declaredEdits"]
    assert before == [await session.scalar(select(func.count()).select_from(m)) for m in models]
    assert hits == []
    staged = await client.post(f"{API}/action-runs:stage", headers=PLANNER, json=STAGE)
    assert staged.status_code == 201, staged.text
    detail = staged.json()
    record = await session.scalar(select(ActionRun).where(ActionRun.code == detail["id"]))
    expected = deepcopy(record.review_snapshot)
    assert expected["humanChecks"] == detail["humanChecks"]
    assert expected["declaredEdits"] == detail["declaredEdits"] == review["declaredEdits"]
    assert detail["reviewUnavailable"] is None
    loaded = await client.get(f"{API}/action-runs/{record.code}", headers=PLANNER)
    assert loaded.json()["humanChecks"] == expected["humanChecks"]
    assert hits == []
    record.review_snapshot = None
    await session.flush()
    legacy = (await client.get(f"{API}/action-runs/{record.code}", headers=PLANNER)).json()
    assert legacy["reviewUnavailable"] and legacy["humanChecks"] is None and legacy["declaredEdits"] is None


async def test_review_snapshot_cannot_expose_invisible_cross_object(runtime, client):  # noqa: F811
    session, _, cross = runtime
    record = await session.scalar(select(ActionRun).where(ActionRun.code == "OPS-R00"))
    record.action_type_code, record.action_name = cross.code, cross.name
    record.params_text = '{"enabled":true,"otherStore":"OPS-S12"}'
    record.review_snapshot = {"typeVersion": record.type_version,
                              "humanChecks": [{"name": "private OPS-S12"}],
                              "declaredEdits": [{"field": "private OPS-S12"}]}
    await session.flush()
    result = await client.get(f"{API}/action-runs/{record.code}", headers=headers("OPS-LIMIT"))
    assert result.status_code == 200
    assert result.json()["reviewUnavailable"] and result.json()["humanChecks"] is None
    assert result.json()["declaredEdits"] is None and "private OPS-S12" not in result.text
