"""Metadata-only metric publication dispatch and rejection boundaries; no DB/HTTP."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.errors import AppError
from app.modules.static import metric_trial as trial
from app.modules.static import metric_validation as validation
from app.modules.static import release_readiness as readiness
from scripts.check_metric_snapshot_export import fixture


async def main():
    definition = {"id": "M-COMPOSITE", "version": "v1", "def": [],
                  "formula": "METRIC('device_count','v1')-METRIC('view_count','v1')"}
    child_rows = [SimpleNamespace(code=code, api_name=api, version="v1",
                                 status=SimpleNamespace(value="已发布"))
                  for code, api in (("M-DEVICE", "device_count"), ("M-VIEW", "view_count"))]
    session = SimpleNamespace(info={"ontology_id": 8}, scalars=AsyncMock())
    user = object()
    children = [{"id": row.code, "version": "v1", "formula": "COUNT(Asset)", "def": []}
                for row in child_rows]
    with (
        patch.object(validation, "check_func"),
        patch("app.modules.governance.policy.enforce", AsyncMock()),
        patch("app.common.sql_guard.ensure_governed_tables", AsyncMock()),
        patch.object(trial, "execution_bindings", AsyncMock(return_value={})),
        patch.object(trial, "metric_definition", AsyncMock(side_effect=children)),
        patch.object(trial, "compile_metric", return_value=(None, None, None, None, [])) as child_compile,
        patch.object(validation, "compile_metric", side_effect=AssertionError("Old compiler entered")),
    ):
        session.scalars.side_effect = [[], [child_rows[0]], [child_rows[1]]]
        result = await validation.validate_metric_definition(session, user, definition, [])
        assert result["program"] is True and result["hasTimeField"] is False
        assert [item["id"] for item in result["dependencies"]] == [row.code for row in child_rows]
        assert child_compile.call_count == 2
        changed = SimpleNamespace(**{**vars(child_rows[0]), "version": "v2"})
        session.scalars.side_effect = [[], [changed]]
        try:
            await validation.validate_metric_definition(session, user, definition, [])
        except AppError as error:
            assert error.code == "METRIC_PROGRAM_INVALID"
        else:
            raise AssertionError("Changed fixed version passed publication validation")
    obj, gov = fixture()
    with (
        patch.object(validation, "check_func"),
        patch("app.modules.governance.policy.enforce", AsyncMock()),
        patch.object(validation, "ensure_governed_tables", AsyncMock()),
        patch.object(validation, "execution_bindings", AsyncMock(return_value={})),
        patch.object(trial, "governed_tables", return_value={gov.table.name: gov}),
    ):
        result = await validation.validate_metric_definition(
            session, user, {"id": "M-PLAIN", "formula": "COUNT(Asset)", "def": []}, [obj]
        )
        assert result == {"program": False, "hasTimeField": False}
    target = SimpleNamespace(target_kind="metric", target_id="M-COMPOSITE")
    cr = SimpleNamespace(code="CR-SYNTHETIC")
    session.scalars = AsyncMock(return_value=[])
    for code, status in (("METRIC_PROGRAM_INVALID", 422), ("GOVERNANCE_DENIED", 403)):
        with (
            patch.object(readiness, "check_func"),
            patch.object(readiness, "metric_definition", AsyncMock(return_value=definition)),
            patch.object(readiness, "validate_metric_definition", AsyncMock(side_effect=AppError(
                "synthetic rejection", code=code, status_code=status))),
        ):
            result = await readiness.release_readiness(session, cr, [target], [], user)
            assert result["V5"][0] == "阻断"


def check_callers():
    # Both publication paths retain the dispatcher; no formal DB writes are simulated.
    root = Path(__file__).resolve().parents[1] / "app/modules"
    for path in (root / "static/release_readiness.py", root / "versioning/service.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [node.func.id for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
        assert "validate_metric_definition" in calls and "compile_metric" not in calls


if __name__ == "__main__":
    check_callers()
    asyncio.run(main())
    print("Metric publication passed: shared validation, fixed versions, metadata-only compile, "
          "CR hard rejection and formal/save caller wiring.")
