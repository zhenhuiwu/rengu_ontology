"""Offline MySQL view discovery plus current source-channel contract checks."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Integer
from sqlalchemy.dialects import mysql

from app.modules.auto_ontology import introspect
from app.modules.data import connector

ROOT = Path(__file__).resolve().parents[1]


class Inspector:
    def get_schema_names(self):
        return ["demo"]

    def get_table_names(self, **kwargs):
        return ["base"]

    def get_view_names(self, **kwargs):
        return ["preview_view"]

    def get_columns(self, name, **kwargs):
        assert name == "base"  # View details must never invoke SHOW CREATE VIEW reflection.
        return [{"name": "id", "type": Integer(), "nullable": False}]

    def get_pk_constraint(self, name, **kwargs):
        assert name == "base"
        return {"constrained_columns": ["id"]}

    def get_foreign_keys(self, name, **kwargs):
        assert name == "base"
        return []

    get_indexes = get_foreign_keys
    get_unique_constraints = get_foreign_keys

    def get_table_comment(self, name, **kwargs):
        assert name == "base"
        return {"text": "table"}

    def get_multi_columns(self, **kwargs):
        raise AssertionError("MySQL column catalog should not issue SHOW CREATE VIEW")

    def get_multi_table_comment(self, *, schema, filter_names):
        assert filter_names == ["base"]
        return {}


def preview():
    def execute(statement, parameters):
        sql = str(statement)
        assert "information_schema.COLUMNS" in sql and ":schema" in sql
        assert parameters["schema"] == "demo" and parameters["names"]
        assert all(name not in sql for name in parameters["names"])
        return [(name, "id", "bigint", "bigint unsigned", "NO", "")
                for name in parameters["names"]]

    conn = SimpleNamespace(dialect=mysql.dialect(), execute=execute)
    with (
        patch.object(introspect, "inspect", return_value=Inspector()),
        patch.object(connector, "inspect", return_value=Inspector()),
    ):
        listed = connector._list(conn, ["demo"])
        assert {item.name: item.cols for item in listed} == {"base": 1, "preview_view": 1}
        view = introspect.inspect_tables(conn, "demo", ["preview_view"])[0]
        assert view.kind == "view" and not view.pk and not view.unique_sets
        assert view.to_json()["kind"] == "view"
        base = introspect.inspect_tables(conn, "demo", ["base"])[0]
        assert base.pk == ["id"] and "kind" not in base.to_json()


if __name__ == "__main__":
    from check_source_channels import main

    preview()
    asyncio.run(main())
