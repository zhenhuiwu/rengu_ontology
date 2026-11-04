"""Offline assertion that mapping reads do not load object properties."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

SOURCE_PATH = Path(__file__).resolve().parents[1] / "app/modules/data/mappings.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


async def check():
    tree = ast.parse(SOURCE)
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "mapped_objects")
    node.returns = None
    for arg in node.args.args:
        arg.annotation = None
    guards = []

    class Source:
        def in_(self, values):
            assert values == ["DS-042", "Old source name"]
            return "source predicate"

    model = SimpleNamespace(properties=object(), datasource_name=Source(), code=object())

    class Query:
        def options(self, value):
            assert value == ("raise", model.properties)
            guards.append("properties disabled")
            return self

        def where(self, predicate):
            assert predicate == "source predicate"
            return self

        def order_by(self, column):
            assert column is model.code
            return self

    rows = [SimpleNamespace(code="Asset", display_name="设备", source_table="public.asset")]

    class Session:
        async def scalars(self, query):
            assert isinstance(query, Query)
            assert guards == ["properties disabled"]
            return rows

    env = {"select": lambda table: Query(), "ObjectType": model, "raiseload": lambda attr: ("raise", attr)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE_PATH), "exec"), env)
    result = await env["mapped_objects"](Session(), SimpleNamespace(code="DS-042", name="Old source name"))
    assert result == rows
    assert result[0] is rows[0]


if __name__ == "__main__":
    asyncio.run(check())
    print("Datasource mapping read passed: properties disabled, original source predicate/order/rows kept.")
