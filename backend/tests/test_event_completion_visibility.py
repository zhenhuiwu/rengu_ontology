from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.event import exceptions, platform_context


async def test_platform_delivery_view_reuses_action_scope_and_never_changes_manage(monkeypatch):
    user = CurrentUser(
        id="owner", name="owner", ontology_id=2, func={"event-types": "manage", "action-center": "view"}
    )
    event = SimpleNamespace(
        ontology_id=2,
        is_test=False,
        obj_kind=None,
        source="dynamic",
        producer_object_type="ActionRun",
        producer_type="ActionExecuted",
        object_id="ACT-1",
        payload={"runId": "ACT-1"},
        definition_snapshot={
            "id": "ActionExecuted",
            "api": "ActionExecuted",
            "source": "动作",
            "objectApi": "ActionRun",
            "objKind": None,
        },
    )
    session = SimpleNamespace(info={"ontology_id": 2}, scalar=AsyncMock(return_value=7))
    from app.modules.dynamic import row_scope

    original_scope = AsyncMock(side_effect=lambda statement, *_: statement)
    monkeypatch.setattr(row_scope, "scoped_instances", original_scope)
    await exceptions.authorize(session, user, event)
    original_scope.assert_awaited_once()
    assert "action_run.ontology_id" in str(original_scope.call_args.args[0])
    assert original_scope.call_args.args[1] is user
    production_gate = AsyncMock(side_effect=AppError("denied", status_code=403))
    monkeypatch.setattr(exceptions, "authorize_object", production_gate)
    with pytest.raises(AppError):
        await exceptions.authorize(session, user, event, "manage")
    production_gate.assert_awaited_once()
    for restricted in (
        replace(user, func={"event-types": "view"}),
        replace(user, ontology_id=3),
        replace(user, api_scopes=("meta:read",)),
    ):
        with pytest.raises(AppError):
            await platform_context.authorize_completion_view(session, restricted, event)
    session.scalar.return_value = None
    with pytest.raises(AppError) as invisible:
        await platform_context.authorize_completion_view(session, user, event)
    assert invisible.value.status_code == 404
    event.payload = {"runId": "different-run"}
    assert not await platform_context.authorize_completion_view(session, user, event)
