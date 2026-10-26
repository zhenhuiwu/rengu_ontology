"""Small trust-boundary checks; no database or external network."""

import pytest
from pydantic import ValidationError
from sqlalchemy import Boolean, Column, String

from app.core.errors import AppError
from app.modules.integration.action_edits import column_value
from app.modules.integration.adapters import AdapterConfig
from app.modules.integration.execution import ExecutionResponse


def test_action_execution_values_reject_ambiguous_or_unsafe_input():
    assert column_value(Column("flag", Boolean, nullable=False), False) is False
    with pytest.raises(AppError):
        column_value(Column("flag", Boolean, nullable=False), 1)
    with pytest.raises(AppError):
        column_value(Column("label", String(3), nullable=False), "long")
    with pytest.raises(ValidationError):
        AdapterConfig(submit_path="//other.example/write")
    with pytest.raises(ValidationError):
        ExecutionResponse(status="SUCCESS", receipt_id="bad\x00receipt")
