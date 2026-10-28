"""Small isolated checks left unrun per the user's no-backend-tests constraint."""

from app.contracts.functions import typed
from app.core.errors import AppError
from app.modules.integration.action_edits import explicit_target_params, mapping_json


def test_explicit_mapping_boundaries():
    definition = {
        "writeback": {
            "edits": [
                {"field": '{"target":{"param":"otherOrder"},"values":{"status":{"param":"status"}}}'},
                {"field": "将订单更新为下一个合法状态"},
            ]
        }
    }
    assert explicit_target_params(definition) == {"otherOrder"}
    assert mapping_json('{"status":{"value":false}}')["status"]["value"] is False
    typed({"items": [0, 1]}, "dict[str, list[int]]")
    for value, annotation in [(True, "int"), (1, "bool")]:
        try:
            typed(value, annotation)
        except AppError:
            pass
        else:
            raise AssertionError("Production function types must not coerce bool/int")
    try:
        mapping_json("修改订单")
    except AppError:
        pass
    else:
        raise AssertionError("Natural language must never become executable mappings")
