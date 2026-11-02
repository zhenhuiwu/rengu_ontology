"""Local retained checks: mocks and SQL compilation only; no database or workers."""

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_seed_events import audit
from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from typer.testing import CliRunner

from app.cli import cli
from app.db.dialects.postgres import PostgresAdapter
from app.modules.event.workspace_defaults import initialize_workspace_events
from app.seeds.bootstrap import initialize_system
from app.seeds.loader import run_seeds


class CheckBase(DeclarativeBase):
    pass


class SeedRow(CheckBase):
    __tablename__ = "check_seed_row"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True)
    is_seed: Mapped[bool] = mapped_column(Boolean)


async def check():
    for env, ontology in (("prod", 1), ("dev", 2), ("dev", None)):
        session = SimpleNamespace(info={"ontology_id": ontology}, execute=AsyncMock())
        with patch("app.seeds.loader.get_settings", return_value=SimpleNamespace(env=env)):
            try:
                await run_seeds(session)
            except Exception as error:
                assert "种子" in str(error) or "工作空间" in str(error)
            else:
                raise AssertionError("Unsafe seed scope accepted")
        session.execute.assert_not_awaited()
    for count in (0, 1):
        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(rowcount=count)))
        try:
            await PostgresAdapter().upsert(
                session, SeedRow, [{"code": "demo", "is_seed": True}], ("code",), seed_only=True
            )
        except ValueError:
            assert count == 0
        else:
            assert count == 1
        statement = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
        assert "WHERE check_seed_row.is_seed IS true" in statement
    session = SimpleNamespace(
        info={},
        execute=AsyncMock(),
        scalars=AsyncMock(side_effect=[[], []]),
        scalar=AsyncMock(side_effect=[None, False, 0, 0]),
        get=AsyncMock(return_value=SimpleNamespace(deleted_at=None)),
        add=Mock(),
        flush=AsyncMock(),
    )
    plan = await initialize_system(session)
    assert plan["mode"] == "dry-run" and plan["demoRows"] == 0
    assert plan["createAdmin"] and plan["createOrganization"] and plan["createStandardEvents"]
    assert str(session.execute.call_args_list[0].args[0]) == "SET TRANSACTION READ ONLY"
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.scalars = AsyncMock(return_value=[SimpleNamespace(code="real-user")])
    try:
        await initialize_system(session, apply=True)
    except ValueError as error:
        assert "既有用户" in str(error)
    else:
        raise AssertionError("Existing installation accepted")
    session.add.assert_not_called()

    standard = SimpleNamespace(info={}, execute=AsyncMock(), scalar=AsyncMock(return_value=10))
    await initialize_workspace_events(standard, 1, "U010001", codes={"ActionOutcomeUnknown"})
    standard.scalar.assert_awaited_once()
    assert standard.scalar.call_args.args[0].compile().params["code"] == "ActionOutcomeUnknown"


def check_audit():
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.side_effect = [
        {"id": 1},
        {"rows": 2, "nonseed_rows": 1},
        {"transaction_read_only": "on"},
    ]
    cursor.fetchall.side_effect = [
        [
            {"relname": name, "relrowsecurity": True, "relforcerowsecurity": True}
            for name in ("event", "event_type", "event_payload_field", "event_daily_stat")
        ],
        [{"id": 9, "code": "coupon_redeemed", "is_seed": True}],
        [{"id": 12}],
        [
            {
                "parent_table": "event_type",
                "child_schema": "public",
                "child_table": "event",
                "child_column": "event_type_id",
                "has_seed": True,
            }
        ],
    ]
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.cursor.return_value = cursor
    with (
        patch("psycopg.connect", return_value=connection),
        patch(
            "app.core.config.get_settings",
            return_value=SimpleNamespace(database_url="postgresql://127.0.0.1/audit_fixture"),
        ),
    ):
        report = audit(1, "audit_fixture")
    assert report["mode"] == "read-only" and report["deletableRows"] is None
    assert report["foreignKeyReferences"][0]["nonseed_rows"] == 1
    statements = [str(call.args[0]) for call in cursor.execute.call_args_list]
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert statements[2] == "SET LOCAL ROLE rengu_ontology_scoped"
    assert not any(s.lstrip().startswith(("DELETE", "UPDATE", "INSERT", "ALTER", "DROP")) for s in statements)
    connection.rollback.assert_called_once()


def check_template_backfill_gate():
    # Parse the migration only: do not call upgrade/downgrade or load Alembic state.
    path = (
        Path(__file__).resolve().parents[1] / "migrations" / "versions" / "dynamic_0024_scenario_template.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    upgrade = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade")
    gate = next(
        node for node in upgrade.body if isinstance(node, ast.If) and "demo_seed" in ast.unparse(node.test)
    )
    expression = compile(ast.Expression(gate.test), str(path), "eval")
    for options, should_skip in (({}, True), ({"demo_seed": "false"}, True), ({"demo_seed": "true"}, False)):
        context = SimpleNamespace(get_x_argument=Mock(return_value=options))
        assert eval(expression, {"context": context}) is should_skip
    assert isinstance(gate.body[0], ast.Return)
    assert gate.lineno < next(
        node.lineno
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "op.get_bind"
    )
    downgrade = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )
    assert isinstance(downgrade.body[0], ast.Raise)

    from app.modules.dynamic.seeds.rules import RULE_SEEDS
    from app.seeds import SeedContext
    from app.seeds.loader import _load_rows

    spec = next(spec for spec in RULE_SEEDS if spec.model.__tablename__ == "scenario_template")
    root = Path(__file__).resolve().parents[1] / "seeds" / "json" / "dynamic"
    context = SeedContext(root)
    rows = _load_rows(context, spec)
    assert len(rows) == len(context.load("SC_TEMPLATES.json"))
    assert [r["sort"] for r in rows] == list(range(len(rows)))
    assert [r["definition"] for r in rows] == context.load("SC_TEMPLATES.json")


if __name__ == "__main__":
    # CLI rejects omissions/mismatches before touching a connection.
    with patch("app.db.session.get_sessionmaker", side_effect=AssertionError("Must not connect")):
        assert CliRunner().invoke(cli, ["seed"]).exit_code != 0
        assert (
            CliRunner()
            .invoke(cli, ["system-init", "--database", "__not_the_configured_database__"])
            .exit_code
            != 0
        )
    asyncio.run(check())
    check_audit()
    check_template_backfill_gate()
    print("Seed boundaries, nonseed conflict SQL and read-only bootstrap checks passed (no database).")
