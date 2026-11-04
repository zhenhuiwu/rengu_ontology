"""Compile stable relationship paths with no database or HTTP."""

from dataclasses import replace
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.dialects import postgresql

from app.common.relation_resolver import path_metadata, path_rows, resolve_path
from app.common.row_filter import Condition, LinkPath, to_sql
from app.core.errors import AppError


def check():
    metadata = MetaData()
    tables = [
        Table(
            f"node{i}",
            metadata,
            Column("id", Integer),
            Column("next_id", Integer),
            Column("ontology_id", Integer),
            Column("label", String),
        )
        for i in range(5)
    ]
    links = [{} for _ in tables]
    for i in range(4):
        links[i][f"REL-{i}"] = LinkPath(
            f"REL-{i}",
            tables[i].c.next_id,
            tables[i + 1],
            tables[i + 1].c.id,
            {"label": tables[i + 1].c.label},
            target_links=links[i + 1],
            ontology_id=7,
            target_object=f"OBJ{i + 1}",
        )
    sentinel = "x' OR 1=1 --"
    for length in range(1, 5):
        path = ".".join([*(f"REL-{i}" for i in range(length)), "label"])
        expr = to_sql(
            Condition(field=path, op="=", value=sentinel), {"next_id": tables[0].c.next_id}, {}, links[0]
        )
        compiled = expr.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert sql.count("EXISTS") == length and sentinel not in sql and sentinel in compiled.params.values()
        for i in range(length):
            assert f"rls_h{i}" in sql and f"rls_h{i}.ontology_id" in sql
        assert set(compiled.params.values()) == {7, sentinel}
        mapping = SimpleNamespace(links=links[0], table=tables[0], source_sql=None)
        statement, _ = path_rows(mapping, path)
        rows_sql = str(statement.compile(dialect=postgresql.dialect()))
        assert rows_sql.count(" JOIN ") == length and "DISTINCT" in rows_sql
        assert resolve_path(links[0], path)[1] is tables[length].c.label
    assert len(list(path_metadata(links[0]))) == 4
    for bad in ("REL-0.missing", "missing.label", "REL-0.REL-1.REL-2.REL-3.REL-4.label"):
        try:
            resolve_path(links[0], bad)
        except AppError:
            pass
        else:
            raise AssertionError("unknown/long path accepted")
    cross = {**links[0], "REL-0": replace(links[0]["REL-0"], ontology_id=8)}
    try:
        resolve_path(cross, "REL-0.REL-1.label")
    except AppError:
        pass
    else:
        raise AssertionError("mixed ontology hops accepted")
    selflinks = {}
    selflinks["parent"] = LinkPath(
        "parent",
        tables[0].c.next_id,
        tables[0],
        tables[0].c.id,
        {"label": tables[0].c.label},
        target_links=selflinks,
        ontology_id=7,
    )
    sql = str(
        to_sql(
            Condition(field="parent.parent.label", op="=", value="safe"),
            {"next_id": tables[0].c.next_id},
            {},
            selflinks,
        ).compile()
    )
    assert "rls_h0" in sql and "rls_h1" in sql
    try:
        Condition(field="REL-0.label;DROP", op="=", value="x")
    except ValidationError:
        pass
    else:
        raise AssertionError("unsafe identifier accepted")


if __name__ == "__main__":
    check()
    print("one to four hop aliases, ontology isolation and bound values passed")
