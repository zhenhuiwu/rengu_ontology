"""Offline PG metadata permission check; no database, credentials or HTTP."""
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.dialects import postgresql

from app.core.errors import Forbidden
from app.modules.auto_ontology import introspect as metadata
from app.modules.data import connector


class Connection:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self):
        self.calls = 0

    def execute(self, statement, params):
        self.calls += 1
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert params == {"schema": "public"}
        assert "has_schema_privilege" in sql
        assert "has_table_privilege(c.oid,'SELECT') OR has_any_column_privilege(c.oid,'SELECT')" in sql
        return [("table_read", 7), ("column_read", -1)]


def check():
    conn = Connection()
    inspector = SimpleNamespace(
        get_schema_names=lambda: ["public"],
        get_table_names=lambda **_: ["hidden", "table_read", "column_read"],
        get_multi_columns=lambda **kw: {(kw["schema"], name): [] for name in kw["filter_names"]},
        get_multi_table_comment=lambda **_: {},
    )
    with patch.object(connector, "inspect", return_value=inspector):
        rows = connector._list(conn)
    assert {row.name: row.rows for row in rows} == {"table_read": 7, "column_read": -1}
    assert conn.calls == 1  # One permission/statistics query per schema, not per table.
    with patch.object(metadata, "inspect", return_value=inspector):
        try:
            metadata.inspect_tables(conn, "public", ["hidden"])
        except Forbidden as error:
            assert error.status_code == 403 and error.details["tables"] == ["hidden"]
        else:
            raise AssertionError("Explicit unreadable table must fail before reflection")
    conn.dialect = SimpleNamespace(name="mysql")
    before = conn.calls
    assert metadata.readable_table_stats(conn, "public") is None and conn.calls == before
    print("PG metadata visibility: batch table/column grants, explicit denial and non-PG path passed")


if __name__ == "__main__":
    check()
