"""Offline schema serialization and answer-local lookup integration checks; no database."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn, SetColumnComment, SetTableComment
from sqlglot import parse_one

from app.core.enums import LabelEnum
from app.modules.qa import chain
from app.modules.qa.read_cache import read_scope


class State(LabelEnum):
    ready = "待处理"
    done = "已完成"


def schema_check():
    table = sa.Table(
        "MixedCase_Equipment_State_Record",
        sa.MetaData(),
        sa.Column("RecordId", sa.String(64), primary_key=True, comment="记录编号"),
        sa.Column(
            "display_name", sa.String(120), nullable=False, comment='名称\n换行 /* 原文 */ -- 与 "引号"'
        ),
        sa.Column("state", sa.Enum(State), comment="处置状态"),
        sa.Column("ontology_id", sa.Integer, comment="系统字段"),
        sa.Column("secret", sa.String, comment="不允许的字段"),
        comment="设备状态原始说明\n保留全部含义",
    )
    binding = SimpleNamespace(
        source_sql=None, projection={"RecordId", "display_name", "state", "ontology_id"}
    )
    dialect = postgresql.dialect()
    with (
        patch.object(chain, "_qa_tables", return_value=(table,)),
        patch.object(chain, "governed_tables", return_value={table.name: binding}),
    ):
        compact = chain._schema_lines(1)[0]
    assert '"MixedCase_Equipment_State_Record"' in compact and '"RecordId"' in compact
    assert "ontology_id" not in compact and "secret" not in compact
    comments = [json.loads(line.split("-- ", 1)[1]) for line in compact.splitlines() if "-- " in line]
    assert table.comment in comments and table.c.display_name.comment in comments
    assert "处置状态：ready=待处理，done=已完成" in comments
    for column in (table.c.RecordId, table.c.display_name, table.c.state):
        assert str(CreateColumn(column).compile(dialect=dialect)) in compact
    parse_one(compact, dialect="postgres")
    old = "\n".join(
        [
            f"CREATE TABLE {dialect.identifier_preparer.format_table(table)} (\n  "
            + ",\n  ".join(
                str(CreateColumn(c).compile(dialect=dialect))
                for c in (table.c.RecordId, table.c.display_name, table.c.state)
            )
            + "\n);",
            str(SetTableComment(table).compile(dialect=dialect)) + ";",
            *(
                str(SetColumnComment(c).compile(dialect=dialect)) + ";"
                for c in (table.c.RecordId, table.c.display_name)
            ),
            f"COMMENT ON COLUMN {table.name}.state IS '处置状态：ready=待处理，done=已完成';",
        ]
    )
    assert len(compact) < len(old), (len(compact), len(old))


async def lookup_check():
    session = SimpleNamespace(info={"ontology_id": 1})
    table = sa.Table("fixture", sa.MetaData(), sa.Column("label", sa.String))
    binding = SimpleNamespace(source_sql=None)
    calls = []
    count_calls = []

    async def values(s, t, c, limit):
        calls.append((s.info["ontology_id"], limit))
        return ["a", "b"]

    async def count(s, t):
        count_calls.append(s.info["ontology_id"])
        return 3

    @read_scope
    async def answer():
        first = await chain._distinct_values(session, table, table.c.label, 31)
        first.append("private mutation")
        assert await chain._distinct_values(session, table, table.c.label, 31) == ["a", "b"]
        assert await chain._table_count(session, table) == 3
        assert await chain._table_count(session, table) == 3
        await chain._distinct_values(session, table, table.c.label, None)
        session.info["ontology_id"] = 2
        await chain._distinct_values(session, table, table.c.label, 31)
        assert await chain._table_count(session, table) == 3

    with (
        patch.object(chain, "governed_tables", return_value={table.name: binding}),
        patch.object(chain, "_read_distinct_values", values),
        patch.object(chain, "_read_table_count", count),
    ):
        await answer()
    assert calls == [(1, 31), (1, None), (2, 31)], calls
    assert count_calls == [1, 2], count_calls


if __name__ == "__main__":
    schema_check()
    asyncio.run(lookup_check())
    print(
        "PASS: complete compact schema, quoting/comments/enums/visibility and answer-local lookup integration"
    )
