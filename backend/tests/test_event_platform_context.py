"""Finite platform contexts use the existing CR permission gate, never arbitrary internal tables."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.event.platform_context import ACTION_RUN, action_payload, platform_binding
from app.modules.static.cr_write import CrWriteService
from app.modules.static.schemas import ItemsPutIn


def test_action_run_context_and_real_emitter_fields():
    assert platform_binding({"api": "ActionExecuted", "source": "动作", "objKind": ACTION_RUN})
    action_payload(
        {
            key: {"api": key, "type": "字符串", "required": key == "runId"}
            for key in ("runId", "actionType", "targetId", "initiator", "confirmer", "writeback")
        }
    )


@pytest.mark.parametrize(
    "api,source,identity",
    [
        ("ActionExecuted", "动作", "platform:SysUser"),
        ("OtherEvent", "动作", ACTION_RUN),
        ("ActionExecuted", "外部系统", ACTION_RUN),
        ("ActionExecuted", "动作", "store"),
    ],
)
def test_unknown_or_spoofed_platform_context_is_rejected(api, source, identity):
    with pytest.raises(AppError):
        platform_binding({"api": api, "source": source, "objKind": identity})


def test_standard_payload_requires_real_string_run_id():
    with pytest.raises(AppError):
        action_payload({})
    with pytest.raises(AppError):
        action_payload({"runId": {"type": "整数", "required": True}})
    with pytest.raises(AppError):
        action_payload(
            {
                "runId": {"type": "字符串", "required": True},
                "inventedResult": {"type": "字符串", "required": True},
            }
        )


async def test_platform_context_does_not_bypass_event_manage_permission():
    service = CrWriteService(SimpleNamespace())
    service._cr = AsyncMock()
    body = ItemsPutIn.model_validate(
        {
            "target": {"kind": "event", "id": "ActionExecuted"},
            "section": "def",
            "rows": [{"key": "objKind", "op": "add", "after": ACTION_RUN}],
        }
    )
    with pytest.raises(AppError) as caught:
        await service.put_items(
            "WF-CR", CurrentUser(id="viewer", name="只读", func={"event-types": "view"}), body
        )
    assert caught.value.status_code == 403
    service._cr.assert_not_called()
