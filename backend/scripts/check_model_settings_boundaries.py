"""Unexecuted local validation/serialization checks; no database or external model calls."""

import json

from pydantic import SecretStr, ValidationError

from app.core.errors import validation_fields
from app.modules.admin.model_settings import ModelRuntime, ModelSaveIn, ModelValue, public_model


def main():
    payload = {
        "model": "model-name",
        "baseUrl": "https://model.example.test/v1",
        "timeoutSec": 30,
        "enabled": True,
        "lockVersion": 0,
    }
    assert ModelSaveIn.model_validate(payload).api_key is None
    for change in [
        {"apiKey": ""},
        {"apiKey": "secret with whitespace"},
        {"baseUrl": "https://user:secret@model.example.test/v1"},
        {"baseUrl": "https://model.example.test/v1?key=secret"},
        {"baseUrl": "file:///etc/passwd"},
        {"timeoutSec": 0},
        {"extra": True},
    ]:
        try:
            ModelSaveIn.model_validate(payload | change)
        except ValidationError as error:
            assert "secret with whitespace" not in json.dumps(validation_fields(error.errors()))
        else:
            raise AssertionError("An invalid model configuration was accepted")
    value = ModelValue.model_validate({k: v for k, v in payload.items() if k != "lockVersion"})
    runtime = ModelRuntime(value, SecretStr("sample-secret"), "database", "database")
    serialized = json.dumps(public_model("qa", runtime))
    assert "sample-secret" not in serialized and "secret_enc" not in serialized
    assert public_model("qa", runtime)["hasKey"] is True


if __name__ == "__main__":
    main()
