"""Offline column UPDATE regression; no database, credentials or HTTP."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import BigInteger, Column, Integer, MetaData, String, Table

from app.core.errors import AppError
from app.modules.data import governed_binding as binding, governed_router as router
from app.modules.data.models import DsType
from app.modules.static.models import ObjectStatus


async def check():
    table = Table("column_update_example", MetaData(), Column("id", BigInteger, primary_key=True),
        Column("ontology_id", BigInteger), Column("status", String), Column("name", String),
        Column("lock_version", Integer), Column("updated_at", String))
    updates = frozenset({"status", "lock_version", "updated_at"})
    policy = "ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint"

    class Session:
        async def execute(self, statement, params):
            sql = str(statement)
            if "pg_class" in sql:
                assert "has_column_privilege(current_user,c.oid,a.attnum,'UPDATE')" in sql
                return SimpleNamespace(one_or_none=lambda: (True, True, "r", True, list(updates)))
            return SimpleNamespace(all=lambda: [("PERMISSIVE", ["public"], "ALL", policy, policy)])

        async def connection(self):
            async def run_sync(fn):
                return fn(None)
            return SimpleNamespace(run_sync=run_sync)

        async def scalar(self, *_):
            return None

    with patch.object(binding, "Table", return_value=table):
        inspected, granted = await binding.inspect_table(Session(), table.name)
    assert inspected is table and granted == updates and "name" not in granted
    props = [SimpleNamespace(api_name=name, display_name=name, source_column=name, backing_column=None,
        is_pk=name == "id", is_sensitive=False, visibility="normal", transform="") for name in ("id", "status", "name")]
    obj = SimpleNamespace(code="Example", pk_property="id", properties=props, status=ObjectStatus.published,
        source_table=table.name, datasource_name="DS-001", ontology_id=16)
    with patch.object(router, "authorize"), patch.object(router, "object_for", AsyncMock(return_value=(obj, table.name))), \
         patch.object(router, "validate_binding", AsyncMock()), \
         patch.object(router, "inspect_table", AsyncMock(return_value=(table, updates))):
        result = await router.detail("Example", Session(), SimpleNamespace(ontology_id=16))
    assert {c["column"] for c in result["columns"] if c["writable"]} == {"status"}
    source = SimpleNamespace(type=DsType.postgresql, host="127.0.0.1", port=5432, database="fixture")
    settings = SimpleNamespace(database_url="postgresql+asyncpg://local@127.0.0.1/fixture",
        qa_readonly_database_url="postgresql+asyncpg://readonly@127.0.0.1/fixture")
    for rights, requested in ((updates, "name"), (updates - {"updated_at"}, "status")):
        session = SimpleNamespace(scalar=AsyncMock(side_effect=[source, "fixture"]))
        with patch.object(binding, "get_settings", return_value=settings), \
             patch.object(binding, "inspect_table", AsyncMock(return_value=(table, rights))):
            try:
                await binding.validate_binding(session, obj, table.name, ["id", requested], [requested])
            except AppError as error:
                assert error.code == "BINDING_UNAVAILABLE" and "更新权限" in error.message
            else:
                raise AssertionError("Missing business/system column UPDATE must be rejected")
    print("Column UPDATE: reflected grants, exact UI candidate, missing field/system grant rejection passed")


if __name__ == "__main__":
    asyncio.run(check())
