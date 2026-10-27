"""Static schema boundary check; never connects to or modifies a database."""

import ast
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import configure_mappers

from app.db.models import Base


def main():
    configure_mappers()
    migrations = Path(__file__).parents[1] / "migrations/versions"
    tables = set()
    for migration in migrations.glob("*.py"):
        tree = ast.parse(migration.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DOMAIN_TABLES" for t in node.targets
            ):
                tables.update(ast.literal_eval(node.value))
    domain = {
        t.name
        for t in Base.metadata.tables.values()
        if "ontology_id" in t.c and t.name not in {"ontology_member", "ontology_role_binding", "audit_log"}
    }
    assert domain == tables, (domain - tables, tables - domain)
    for name in domain:
        table = Base.metadata.tables[name]
        assert not table.c.ontology_id.nullable, name
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                assert "ontology_id" in constraint.columns, (name, constraint.name)
        for index in table.indexes:
            if index.unique:
                assert "ontology_id" in index.columns, (name, index.name)
    print(f"{len(domain)} ontology tables: static mapper and uniqueness boundaries are consistent")


if __name__ == "__main__":
    main()
