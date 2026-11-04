"""Offline metadata batching and permission-boundary assertions; no database or HTTP."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from app.common import sql_guard
from app.core.errors import AppError
from app.modules.admin import org_scope, permissions
from app.modules.data import governed_binding as gb


async def bindings():
    rows = [
        SimpleNamespace(
            object_code=code,
            table_name=f"fixture_{code.lower()}",
            read_columns=["id"],
            write_columns=[],
            supplementary=None,
        )
        for code in ("A", "B", "BAD", "MISSING")
    ]
    objects = [SimpleNamespace(code=code, datasource_name="DS") for code in ("A", "B", "BAD")]
    source = SimpleNamespace(code="DS", name="fixture", ontology_id=16)
    calls, inspector = [], object()

    class Connection:
        async def run_sync(self, fn):
            return fn(None)

    class Session:
        info = {}

        async def scalar(self, stmt):
            calls.append("database" if "current_database" in str(stmt) else "schema")
            return "fixture" if "current_database" in str(stmt) else True

        async def scalars(self, stmt):
            model = stmt.column_descriptions[0]["entity"]
            calls.append(model.__name__)
            return {gb.GovernedBinding: rows, gb.ObjectType: objects, gb.Datasource: [source]}[model]

        async def connection(self):
            return Connection()

    async def validate(session, obj, *args, **kwargs):
        assert kwargs == {"sources": [source], "database": "fixture", "inspector": inspector}
        if obj.code == "BAD":
            raise gb.invalid("synthetic invalid binding")

    def table(name, metadata, **kwargs):
        assert kwargs["autoload_with"] is inspector
        assert kwargs["resolve_fks"] is False
        assert kwargs["include_columns"] == {"id", "ontology_id"}
        return sa.Table(name, metadata, sa.Column("id", sa.Integer), sa.Column("ontology_id", sa.Integer))

    session = Session()
    with (
        patch.object(gb, "inspect", return_value=inspector),
        patch.object(gb, "validate_binding", validate),
        patch.object(gb, "Table", table),
    ):
        result, blocked = await gb.load_bindings(session, 16)
    assert set(result) == {"fixture_a", "fixture_b"}
    assert blocked == {"A", "B", "BAD", "MISSING"}
    assert calls == ["schema", "GovernedBinding", "ObjectType", "Datasource", "database"]
    assert all(g.projection == frozenset({"id"}) for g in result.values())

    # Batch-supplied missing, foreign-workspace or ambiguous sources fail before reflection.
    obj = SimpleNamespace(
        status=gb.ObjectStatus.published, source_table="fixture_a", datasource_name="DS", ontology_id=16
    )
    foreign = SimpleNamespace(code="DS", name="fixture", ontology_id=17)
    for sources in ([], [foreign], [source, source]):
        with patch.object(gb, "inspect_table", AsyncMock()) as inspect_table:
            try:
                await gb.validate_binding(
                    session, obj, "fixture_a", ["id"], [], sources=sources, database="fixture"
                )
            except AppError as error:
                assert error.code == "BINDING_UNAVAILABLE"
            else:
                raise AssertionError("unsafe source accepted")
            inspect_table.assert_not_awaited()


async def access():
    objects = [
        SimpleNamespace(id=1, code="A", api_name="Alpha", display_name="shared"),
        SimpleNamespace(id=2, code="B", api_name="Beta", display_name="shared"),
    ]
    roles = [
        SimpleNamespace(
            id=10, objs=[{"api": "Alpha", "view": True}, {"api": "Beta", "view": True}], default_deny=True
        )
    ]

    def scope(oid=None, code=None, label="shared", prop="region"):
        return SimpleNamespace(
            role_id=10,
            object_type_id=oid,
            object_type_code=code,
            obj_type=label,
            prop=prop,
            op="=",
            values=["synthetic"],
        )

    scopes = {10: [scope(1), scope(), scope(2, prop=org_scope.ORG_PROP)]}

    class Session:
        info = {"ontology_id": 16}

        def __init__(self):
            self.scope_queries = 0
            self.object_queries = 0

        async def execute(self, stmt):
            names = [d["name"] for d in stmt.column_descriptions]
            if names[0] == "role_id":
                self.scope_queries += 1
                return scopes[10]
            if "objs" in names:
                return roles
            self.object_queries += 1
            if "created_by" in str(stmt):
                return [(1, "A", "Alpha", "shared")]
            return objects

        async def scalar(self, stmt):
            assert "to_regclass" in str(stmt)
            return False

    def effective(code, grants, **kwargs):
        return [(g.default_deny, list(g.scopes)) for g in grants]

    session = Session()
    with (
        patch.object(sql_guard, "ensure_governed_tables", AsyncMock()),
        patch.object(
            sql_guard,
            "governed_tables",
            return_value={"fixture": SimpleNamespace(object_type="A", projection=frozenset({"id"}))},
        ),
        patch.object(sql_guard, "effective_expr", effective),
        patch.object(org_scope, "load_org_fields", AsyncMock(return_value={"B": "department"})),
    ):
        result = await permissions.role_row_access(session, roles)
        assert session.scope_queries == 1 and session.object_queries == 1
        assert [item["prop"] for item in result["A"][0][1]] == ["region", ""]
        assert [item["prop"] for item in result["B"][0][1]] == ["", org_scope.ORG_PROP]
        assert result["B"][0][1][-1]["field"] == "department"
        scopes[10] = [scope(999)]  # Deleted identity must never become unrestricted access.
        result = await permissions.role_row_access(session, roles, scopes=scopes)
        assert result["A"][0][1] == [{"prop": "", "op": "=", "values": []}]
        roles[0].objs.append({"api": "Alpha", "view": True})
        result = await permissions.role_row_access(session, roles, scopes=scopes)
        assert result["A"] == []  # Duplicate grants remain denied.
        roles[0].objs.pop()
        before = session.scope_queries
        member = SimpleNamespace(ontology_id=16, user_id=1, role="owner")
        await permissions.object_row_access(session, 1, member=member, user_code="FIXTURE_USER")
        assert session.scope_queries == before + 1  # Owner and role evaluator share one scope query.


async def main():
    await bindings()
    await access()


if __name__ == "__main__":
    asyncio.run(main())
    print("Metadata read reuse offline check passed: batch counts, projections, invalid bindings and scopes.")
