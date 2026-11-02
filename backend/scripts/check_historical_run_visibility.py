"""Offline fixed-version action visibility; no database, HTTP or execution."""
import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from sqlalchemy.dialects import postgresql
import app.db.models
from app.modules.dynamic import run_visibility as visibility


async def main():
    definition = {"id": "office_action", "version": "v1", "targetKind": "Device", "sysId": "EXT-OFFLINE",
                  "params": [{"api": "score", "type": "整数"}], "writeback": {"edits": [], "effects": []}}
    action = NS(code="office_action", version="v2", retired_release_id=None)
    run = NS(id=1, action_type_code=action.code, type_version="v1", params={"score": 90}, params_text='{"score":90}')
    rows = [(action.code, "v1", definition)]
    session = NS(info={"ontology_id": 16}, scalars=AsyncMock(return_value=[action]), execute=AsyncMock(side_effect=lambda _: rows))
    row_filter = AsyncMock(return_value=None)
    refs = AsyncMock(return_value=set())
    with patch.object(visibility, "action_operation_objects", AsyncMock(return_value=[NS(code="Device")])), patch.object(visibility, "external_webhook", AsyncMock(return_value=False)), patch.object(visibility, "row_filter", row_filter), patch.object(visibility, "visible_references", refs):
        assert await visibility.parameters_visibility(session, NS(), [run, NS(**{**vars(run), "id": 2})]) == {}
        assert session.execute.await_count == 1  # Batch same type/version; never per-row release queries.
        sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "ontology_release.ontology_id = 16" in sql and "v1" in sql and "LIMIT 1" in sql
        assert row_filter.await_args.args[1] == "Device"
        assert run.params == {"score": 90} and run.type_version == "v1"
        rows.clear()
        assert 1 in await visibility.parameters_visibility(session, NS(), [run])  # No borrowing v2 if history absent.
        rows.append((action.code, "v1", definition))
        with patch.object(visibility, "is_deny_all", return_value=True):
            assert 1 in await visibility.parameters_visibility(session, NS(), [run])
        definition["params"] = [{"api": "score", "type": "对象引用 · Device"}]
        run.params = {"score": "another"}
        with patch.object(visibility, "object_binding", AsyncMock(return_value=(NS(code="Device"), NS()))):
            assert 1 in await visibility.parameters_visibility(session, NS(), [run])  # Hidden references remain hidden.
        assert refs.await_args.args[2] == {("Device", "another")}
    print("historical run visibility: fixed v1/batched scoped SQL/missing snapshot/current scope/reference denial passed (offline)")


if __name__ == "__main__":
    asyncio.run(main())
