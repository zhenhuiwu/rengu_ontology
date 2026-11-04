"""Offline expression/physical/mirror whitelist checks; no database or HTTP."""

from types import SimpleNamespace

from sqlalchemy import Column, Integer, MetaData, String, Table, func, literal, select
from sqlalchemy.dialects import postgresql

from app.common.object_identity import source_columns
from app.common.row_filter import to_sql


def check():
    physical = Table(
        "fixture_product",
        MetaData(),
        Column("id", Integer),
        Column("category", String),
        Column("private_column", String),
    )
    category = func.split_part(physical.c.category, literal("/"), literal(1))
    mapping = SimpleNamespace(table=physical, columns={"id": physical.c.id, "category_l1": category})
    columns = source_columns(mapping, physical)
    assert columns["id"] is physical.c.id and columns["category_l1"] is category
    assert "private_column" not in columns and "category" not in columns
    columns.clear()
    assert len(mapping.columns) == 2
    expression = {"field": "category_l1", "op": "=", "value": "synthetic"}
    predicate = to_sql(expression, source_columns(mapping, physical), {})
    sql = str(select(physical.c.id).where(predicate).compile(dialect=postgresql.dialect()))
    assert "split_part(fixture_product.category" in sql

    mirror = select(literal(1).label("id"), literal("synthetic").label("category_l1")).subquery("mirror")
    mirrored = source_columns(mapping, mirror)
    assert mirrored["id"] is mirror.c.id
    assert "category_l1" not in mirrored  # Never reuse the original table's computed expression.
    mapped_column = SimpleNamespace(table=physical, columns={"category_l1": physical.c.category})
    assert "category_l1" not in source_columns(mapped_column, mirror)
    real_mirror = select(literal("synthetic").label("category")).subquery("real_mirror")
    rebound = source_columns(mapped_column, real_mirror)
    assert rebound["category_l1"] is real_mirror.c.category
    compiled = str(
        select(real_mirror).where(to_sql(expression, rebound, {})).compile(dialect=postgresql.dialect())
    )
    assert "fixture_product" not in compiled


if __name__ == "__main__":
    check()
    print("Object scope columns offline check passed: physical expression whitelist and mirror isolation.")
