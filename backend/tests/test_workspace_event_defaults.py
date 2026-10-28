from app.modules.event.workspace_defaults import STANDARD_EVENTS


def test_standard_events_match_action_emitter_payloads():
    definitions = {code: {api: kind for api, _, kind in fields} for code, _, fields in STANDARD_EVENTS}
    assert definitions == {
        "ActionExecuted": {
            "runId": "字符串", "actionType": "字符串", "targetId": "字符串",
            "initiator": "字符串", "confirmer": "字符串", "writeback": "字符串",
        },
        "ActionOutcomeUnknown": {
            "runId": "字符串", "actionType": "字符串", "system": "字符串",
            "idemKey": "字符串", "waitedMs": "整数",
        },
    }
