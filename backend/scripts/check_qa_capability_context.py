"""Offline capability-view/cache assertions using current source and fake reads."""

import ast
import asyncio
import sys
from copy import deepcopy
from itertools import product
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.modules.qa.read_cache import cached_read, read_scope  # noqa: E402


def source_functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    nodes = [
        n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names
    ]
    assert {n.name for n in nodes} == set(names)
    for node in nodes:
        node.returns = None
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            arg.annotation = None
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


class Query:
    def where(self, *args):
        return self

    def order_by(self, *args):
        return self


async def check():
    statuses = ("all", "scoped", "none", None, "future")
    rows = [
        SimpleNamespace(
            code=str(i), api_name=str(i), display_name=str(i), obj_group="g", description="d", properties=[]
        )
        for i in range(len(statuses))
    ]

    async def row_filter(user, code):
        return None if user.access[int(code)] == "all" else user.access[int(code)]

    class Session:
        def __init__(self, oid):
            self.oid = oid

        async def scalars(self, query):
            return rows

    sections = tuple((k, k) for k in ("objects", "metrics", "functions", "actions", "rules", "events"))
    raw_env = {
        "select": lambda model: Query(),
        "ObjectType": SimpleNamespace(status=0, seq=0, id=0),
        "ObjectStatus": SimpleNamespace(published=0),
        "row_filter": row_filter,
        "is_deny_all": lambda x: x == "none",
        "SECTIONS": sections,
        "NOTES": ["data"],
        "Forbidden": PermissionError,
    }
    source_functions(
        Path("app/modules/static/capabilities.py"),
        {"_can_view", "_row_access", "_visible_props", "_objects", "capabilities"},
        raw_env,
    )

    async def metrics(session, user):
        return [{"id": "metric", "scope": session.oid}]

    async def functions(session):
        return [{"id": "function"}]

    async def actions(session, user, objects, access, key_context):
        return [{"id": "action", "objects": list(objects), "access": dict(access), "key": key_context}]

    async def rules(session, user, access):
        return [{"id": "rule", "access": dict(access)}]

    async def events(session, user, access, key_context):
        return [{"id": "event", "access": dict(access), "key": key_context}]

    raw_env.update(_metrics=metrics, _functions=functions, _actions=actions, _rules=rules, _events=events)
    raw = raw_env["capabilities"]
    calls = []

    async def counted(session, user, request=None, **kwargs):
        calls.append((id(session), session.oid, id(user), id(request)))
        return await raw(session, user, request, **kwargs)

    env = {
        "deepcopy": deepcopy,
        "cached_read": cached_read,
        "_capabilities": counted,
        "require_ontology_id": lambda s: s.oid,
    }
    source_functions(Path("app/modules/qa/capability_context.py"), {"capabilities"}, env)
    cached = env["capabilities"]
    session, request = Session(1), SimpleNamespace(state=SimpleNamespace(api_key={"scopes": ["meta:read"]}))

    @read_scope
    async def modes(user, req):
        before = len(calls)
        full = await cached(session, user, req)
        visible = await cached(session, user, req, readable_only=True)
        assert full == await raw(session, user, req)
        assert visible == await raw(session, user, req, readable_only=True)
        full["notes"].append("mutation")
        for item in full.get("objects", []):
            item["properties"].append({"api": "mutation"})
        assert await cached(session, user, req) == await raw(session, user, req)
        assert len(calls) == before + 1

    for flags in product((False, True), repeat=len(sections)):
        if not any(flags):
            continue
        user = SimpleNamespace(
            func={name: "view" if flag else "none" for (name, _), flag in zip(sections, flags, strict=True)},
            access=statuses,
        )
        await modes(user, request)
        await modes(user, None)

    user = SimpleNamespace(func={name: "view" for name, _ in sections}, access=statuses)
    other_user = SimpleNamespace(func=dict(user.func), access=statuses)

    @read_scope
    async def separation():
        before = len(calls)
        await cached(session, user, request)
        await cached(session, other_user, request)
        session.oid = 2
        await cached(session, user, request)
        await cached(Session(2), user, request)
        await cached(session, user, SimpleNamespace(state=SimpleNamespace(api_key=None)))
        assert len(calls) == before + 5

    await separation()
    before = len(calls)
    await modes(user, request)
    await modes(user, request)
    assert len(calls) == before + 2

    async def future_catalog(*args, **kwargs):
        return {"objects": [{"id": str(i), "rowAccess": value} for i, value in enumerate(statuses)]}

    env["_capabilities"] = future_catalog
    future = await cached(session, user, request, readable_only=True)
    assert [o["rowAccess"] for o in future["objects"]] == [v for v in statuses if v != "none"]
    for name in ("intent.py", "definition_answers.py"):
        tree = ast.parse((ROOT / "app/modules/qa" / name).read_text(encoding="utf-8"))
        assert any(
            isinstance(n, ast.ImportFrom)
            and n.module == "app.modules.qa.capability_context"
            and any(a.name == "capabilities" for a in n.names)
            for n in ast.walk(tree)
        )


if __name__ == "__main__":
    asyncio.run(check())
    print(
        "QA capability context passed: current modes, permissions/key context, "
        "one read, isolation and copy safety."
    )
