"""Opt-in boundary self-check; no database, HTTP, worker, migration or LLM execution.

Run manually from backend: python scripts/check_ontology_runtime_boundaries.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common.sql_guard import SqlRejected, governed_tables, run_guarded
from app.core.errors import AppError
from app.modules.biz import governance as _governance  # noqa: F401
from app.modules.dynamic.scenario_templates import templates
from app.modules.event import delivery, ingress_schema
from app.modules.integration import adapters
from app.modules.qa.explore_api import bindings


class Connection:
    def __init__(self):
        self.calls = []

    async def exec_driver_sql(self, sql, params):
        self.calls.append((sql, params))


async def main():
    assert governed_tables(1) and not governed_tables(2) and not governed_tables()
    assert not bindings(2) and not templates(2)
    user = SimpleNamespace(ontology_id=1, attrs={}, row_filters={"store": None, "product": None})
    connection = Connection()
    await run_guarded(connection, "SELECT name FROM store UNION ALL SELECT name FROM product", user)
    sql, params = connection.calls.pop()
    assert sql.count("ontology_id") == 2 and params == (1, 1)
    for sql, scope in [("SELECT * FROM store", 2), ("SELECT set_config('role', 'postgres', true)", 1)]:
        user.ontology_id = scope
        try:
            await run_guarded(connection, sql, user)
        except SqlRejected:
            pass
        else:
            raise AssertionError("Unbound workspace or configuration-changing SQL was accepted")
        assert not connection.calls

    with patch.object(
        ingress_schema,
        "get_settings",
        return_value=SimpleNamespace(
            internal_event_token=SecretStr("test-only-legacy-token"),
            internal_event_tokens=None,
        ),
    ):
        try:
            await ingress_schema.authenticate(None, "Bearer test-only-legacy-token", "2")
        except AppError as exc:
            assert exc.code == "EVENT_INGRESS_UNAVAILABLE"
        else:
            raise AssertionError("Legacy credential authorized another workspace")

    event = SimpleNamespace(ontology_id=2, definition_snapshot={"id": "same-code"})
    subscriber = SimpleNamespace(subscriber_name="same-subscriber")
    with patch.object(delivery, "routes", return_value={"same-code/same-subscriber": {"user": "legacy"}}):
        assert delivery.route_for(event, subscriber) == {}
    settings = SimpleNamespace(
        integration_real_connect=True, integration_adapters=SecretStr('{"same-code":{"queryPath":"/query"}}')
    )
    system = SimpleNamespace(code="same-code", ontology_id=2, status="启用")
    with patch.object(adapters, "get_settings", return_value=settings):
        try:
            adapters.get_adapter(system)
        except AppError as exc:
            assert exc.code == "RECONCILE_UNAVAILABLE"
        else:
            raise AssertionError("A new workspace borrowed the legacy adapter binding")


if __name__ == "__main__":
    asyncio.run(main())
