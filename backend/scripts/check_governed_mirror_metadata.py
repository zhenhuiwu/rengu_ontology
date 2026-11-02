"""Offline check of mirror metadata loading; no application startup, engines, or sockets."""

import ast
import asyncio
import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS

from sqlalchemy import Column, ForeignKey, Integer, String, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class ObjectType(Base):
    __tablename__ = "object_type"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    ontology_id = Column(Integer)
    properties = relationship("Property")


class Property(Base):
    __tablename__ = "property"
    id = Column(Integer, primary_key=True)
    object_type_id = Column(ForeignKey("object_type.id"))


class ExternalReadBinding(Base):
    __tablename__ = "external_read_binding"
    id = Column(Integer, primary_key=True)
    object_code = Column(String, unique=True)
    ontology_id = Column(Integer)
    datasource_code = Column(String)


class Datasource(Base):
    __tablename__ = "datasource"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True)
    ontology_id = Column(Integer)


class ExternalMirrorState(Base):
    __tablename__ = "external_mirror_state"
    id = Column(Integer, primary_key=True)


class ObjectStatus(Enum):
    published = "published"


class FixtureImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        return None if (node.module or "").startswith("app.") else node


root = Path(__file__).resolve().parents[1]
source = ast.parse((root / "app/modules/data/external_mirror.py").read_text(encoding="utf-8"))
names = {
    "governed_mirrors", "signature", "_legacy_signature", "signature_matches", "mirror_metadata",
    "mirror_binding_signatures", "current_rows_sql", "_sql_text", "_sql_ident", "_logical_name",
    "_internal_column",
}
functions = [node for node in source.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]


async def schema_ready(session):
    return session.ready


namespace = dict(globals(), GovernedTable=lambda *args, **kwargs: NS(table=args[0], object_type=args[1], **kwargs),
                 scan_schema_ready=schema_ready,
                 primary_property=lambda obj: next((p for p in obj.properties if p.is_pk), None),
                 _IDENT=re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"), _CASTS={"string": "text"})
exec(compile(FixtureImports().visit(ast.Module(body=functions, type_ignores=[])), "mirror-metadata-only", "exec"), namespace)


class Session:
    def __init__(self, rows, states=None, ready=True):
        self.rows = rows
        self.states = states if states is not None else [fixture[0] for fixture in rows]
        self.ready = ready
        self.queries = []

    async def scalars(self, statement):
        self.queries.append(statement)
        name = statement.column_descriptions[0]["entity"].__tablename__
        if name == "external_mirror_state":
            return self.states
        assert name == "external_read_binding"
        return [binding for _, _, binding, _ in self.rows if binding is not None]

    async def execute(self, statement):
        self.queries.append(statement)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert sql.count("LEFT OUTER JOIN") == 2
        assert all(f"{table}.ontology_id =" in sql for table in ("object_type", "external_read_binding", "datasource"))
        assert "object_type.code IN" in sql and len(statement._with_options) == 1
        seen = {state.object_code for state in self.states}
        return [(obj, binding, src) for _, obj, binding, src in self.rows if obj is not None and obj.code in seen]


def fixture(code):
    prop = NS(api_name="key", source_column="key", data_type="string", is_pk=True,
              is_sensitive=False, visibility="visible", transform=None, display_name="Key", lock_version=1)
    obj = NS(code=code, display_name=code, status=ObjectStatus.published, pk_property="key", properties=[prop],
             datasource_name="source", source_table="demo.records", lock_version=1)
    binding = NS(object_code=code, datasource_code="source", schema_name="demo", table_name="records",
                 read_columns=["key"], lock_version=1)
    src = NS(code="source", name="Source", type=NS(value="dm8"), host="fixture.invalid", port=1234,
             database="fixture", username="fixture", ssl=False, password_enc="", lock_version=1)
    state = NS(object_code=code, binding_signature=namespace["signature"](binding, src, obj),
               status="succeeded", complete=True, cursor_value={"deletionsAligned": True})
    return state, obj, binding, src


async def main():
    run = namespace["governed_mirrors"]
    for size in (1, 12):
        session = Session([fixture(f"item_{i}") for i in range(size)])
        tables, gaps = await run(session, 17, set(), set())
        assert len(tables) == size and gaps == [] and len(session.queries) == 3
        assert all(table.projection == frozenset({"key"}) and "ontology_id = 17" in table.source_sql for table in tables.values())
    valid = fixture("valid")
    missing = fixture("missing")
    no_binding = fixture("no_binding")
    no_source = fixture("no_source")
    stale = fixture("stale")
    stale[0].binding_signature = "0" * 64
    private = fixture("private")
    private[1].properties[0].is_sensitive = True
    private[0].binding_signature = namespace["signature"](private[2], private[3], private[1])
    rows = [valid, (missing[0], None, None, None), (no_binding[0], no_binding[1], None, None),
            (no_source[0], no_source[1], no_source[2], None), stale, private]
    tables, gaps = await run(Session(rows), 17, set(), set())
    assert set(tables) == {"valid"} and set(gaps) == {"no_binding", "no_source", "stale", "private"}
    tables, gaps = await run(Session([valid]), 17, set(), {"valid"})
    assert tables == {} and gaps == []
    tables, gaps = await run(Session([valid]), 17, {"valid"}, set())
    assert tables == {} and gaps == ["valid"]
    unpublished = fixture("unpublished")
    unpublished[1].status = "draft"
    restart = fixture("restart")
    restart[0].cursor_value = {"restartRequired": True}
    assert await run(Session([unpublished, restart]), 17, set(), set()) == ({}, ["unpublished", "restart"])
    orphan = fixture("orphan")
    tables, gaps = await run(Session([valid, orphan], states=[valid[0]]), 17, set(), set())
    assert set(tables) == {"valid"} and gaps == ["orphan"]
    empty = Session([])
    assert await run(empty, 17, set(), set()) == ({}, []) and len(empty.queries) == 2
    pending = Session([valid], ready=False)
    assert await run(pending, 17, set(), set()) == ({}, ["valid"]) and len(pending.queries) == 1
    print("OK: batched metadata, scoped joins, valid projections and unavailable/private metadata; no DB/network")


if __name__ == "__main__":
    asyncio.run(main())
