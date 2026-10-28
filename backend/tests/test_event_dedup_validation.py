from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import AppError
from app.modules.event.dedup_validation import validate_draft_window, validate_window


def test_windows_reuse_ingress_contract_and_name_the_field():
    assert validate_window("24 小时").total_seconds() == 86400
    assert validate_window("永久") is None
    for value in ("", "0 天", "明天", 24, None):
        with pytest.raises(AppError) as rejected:
            validate_window(value)
        assert rejected.value.details["field"] == "dedupWindow"


async def test_new_and_changed_windows_validate_without_rewriting_legacy_values():
    session = SimpleNamespace(scalar=AsyncMock(return_value=None), scalars=AsyncMock(return_value=[]))
    with pytest.raises(AppError):
        await validate_draft_window(session, "new", 1, section="def")
    await validate_draft_window(
        session, "new", 1, section="def", rows=[SimpleNamespace(key="dedupWindow", op="add", after="24 小时")]
    )
    session.scalar.return_value = SimpleNamespace(dedup_window="")
    await validate_draft_window(session, "legacy", 1, section="def")
    await validate_draft_window(session, "legacy", 1, complete=True)
    for operation in ("update", "delete"):
        with pytest.raises(AppError):
            await validate_draft_window(
                session,
                "legacy",
                1,
                section="def",
                rows=[SimpleNamespace(key="dedupWindow", op=operation, after="")],
            )
    session.scalars.return_value = [SimpleNamespace(op="update", after="0 天")]
    with pytest.raises(AppError):
        await validate_draft_window(session, "legacy", 1, complete=True)
