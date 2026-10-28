from pydantic import ValidationError

from app.modules.dynamic.cr_link import rule_sections
from app.modules.dynamic.rules_schemas import AutoStageIn


def test_rule_execution_mode_contract():
    assert AutoStageIn(enabled=True).execution_mode == "confirm"
    assert AutoStageIn(enabled=True, executionMode="auto").execution_mode == "auto"
    try:
        AutoStageIn(enabled=True, executionMode="invalid")
    except ValidationError:
        pass
    else:
        raise AssertionError("unknown execution modes must be rejected")
    old = {"autoStage": {"enabled": True}}
    unchanged = {"autoStage": {"enabled": True, "executionMode": "confirm"}}
    automatic = {"autoStage": {"enabled": True, "executionMode": "auto"}}
    assert not rule_sections(old, unchanged)["config"]
    assert rule_sections(old, automatic)["config"][0]["key"] == "autoStage.executionMode"
