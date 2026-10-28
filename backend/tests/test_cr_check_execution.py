"""A check can pass only when this validator actually ran it; no database or external executor."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.modules.static.cr_write import CrWriteService
from app.modules.static.models import CRLevel


@pytest.mark.parametrize("legacy", [False, True])
async def test_unsupported_checks_skip_and_legacy_block_is_preserved(monkeypatch, legacy):
    monkeypatch.setattr("app.modules.static.cr_analysis.refresh_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr("app.modules.static.release_readiness.release_readiness", AsyncMock(return_value={}))
    session = SimpleNamespace(execute=AsyncMock(), add=Mock())
    service = CrWriteService(session)
    service.targets = AsyncMock(
        return_value=[SimpleNamespace(target_kind="object", target_id="store", is_new=True)]
    )
    service.items = AsyncMock(return_value=[])
    service._baseline = AsyncMock(
        return_value={"V14": {"result": "阻断", "note": "原有阻断"}} if legacy else {}
    )
    service._touch = Mock()
    cr = SimpleNamespace(id=100, code="WF-CHECK", level=CRLevel.minor, changes=1, detail={})

    result = await service.run_checks(cr, event=False)
    checks = {row["code"]: row for row in result["checks"]}
    assert checks["V2"]["result"] == "不适用" and "未执行" in checks["V2"]["note"]
    assert checks["V12"]["result"] == "通过" and checks["V12"]["note"]
    if legacy:
        assert checks["V14"]["result"] == "阻断" and result["blocked"]
    else:
        assert checks["V14"]["result"] == "不适用" and "未执行" in checks["V14"]["note"]
        assert not result["blocked"]
    assert all(row["note"] for row in result["checks"])
