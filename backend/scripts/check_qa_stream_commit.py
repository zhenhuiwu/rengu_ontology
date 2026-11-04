"""Offline controller check using the actual ask_stream AST and in-memory collaborators."""

import ast
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import SimpleNamespace

from anyio import CancelScope
from fastapi.dependencies.models import Dependant, _get_cache_key

from app.modules.governance.policy import decide
from app.modules.governance.query_guard import bind_request_context, request_session

SOURCE = Path(__file__).resolve().parents[1] / "app/modules/qa/router.py"
PARSED = ast.parse(SOURCE.read_text(encoding="utf-8"))


async def check():
    def session_provider():
        yield None

    assert _get_cache_key(dependant=Dependant(call=session_provider, scope="function")) != _get_cache_key(
        dependant=Dependant(call=session_provider, scope="request")
    )
    principal = SimpleNamespace(ontology_id=16)
    target = SimpleNamespace(info={"data_purpose": "old"})
    bind_request_context(target, principal, SimpleNamespace(headers={}))
    assert target.info["data_purpose"] is None and target.info["governance_user"] is principal
    definition = {
        "resources": [{}],
        "grants": [
            {"role": "R", "purposes": ["verification"], "classes": [], "operations": ["read"], "unmask": True}
        ],
    }
    states = {
        "P": {
            "protected": True,
            "unresolved": False,
            "classes": [],
            "level": None,
            "purposes": ["verification"],
            "mask": "null",
        }
    }
    assert decide(definition, states, ["R"], "P", "read", "verification")["allowed"]
    assert not decide(definition, states, [], "P", "read", "verification")["allowed"]
    assert not decide(definition, states, ["R"], "P", "read", "invented")["allowed"]
    node = next(n for n in PARSED.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "ask_stream")
    node.decorator_list = []
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)

    async def run_case(*, fail_commit=False, disconnect=False, purpose="verification"):
        calls, cleaned, contexts = [], [], []

        class Session:
            committed = False
            active = True

            def __init__(self):
                self.info = {"data_purpose": "old"}

            async def commit(self):
                calls.append("commit")
                if fail_commit:
                    raise RuntimeError("synthetic commit failure")
                self.committed, self.active = True, False

            async def rollback(self):
                calls.append("rollback")
                self.active = False

            def in_transaction(self):
                return self.active

        session = Session()

        async def scope(session, oid):
            session.info["ontology_id"] = oid

        async def effective_user(*args):
            return 1, None

        async def detail(*args):
            return SimpleNamespace(owner_id=1)

        @asynccontextmanager
        async def opener(*args):
            try:
                yield object()
            finally:
                cleaned.append("connection")

        async def ask_once(*args, on_status):
            context = request_session.get()
            assert context["session"] is session and context["active"] is True
            assert session.info["data_purpose"] == purpose
            assert session.info["governance_user"].ontology_id == 16
            contexts.append(context)
            try:
                on_status("compiling", "status")
                if disconnect:
                    await asyncio.Event().wait()
                return object()
            finally:
                cleaned.append("work")

        async def events(*args):
            assert not session.committed
            calls.append("stored_events")
            return [(1, "text", {"full": "answer"}), (2, "done", {"kind": "answer"})]

        env = dict(
            asyncio=asyncio,
            CancelScope=CancelScope,
            suppress=suppress,
            set_ontology_scope=scope,
            svc=SimpleNamespace(effective_user=effective_user),
            r=SimpleNamespace(SessionRepo=lambda _: SimpleNamespace(detail=detail)),
            _ask_once=ask_once,
            AppError=type("AppError", (Exception,), {}),
            stream=SimpleNamespace(stored_events=events, sse=lambda name, data, eid=None: (name, data)),
            _log=SimpleNamespace(exception=lambda *args: None),
            StreamingResponse=lambda body, **kwargs: body,
        )
        env["stream"].MEDIA_TYPE, env["stream"].HEADERS = "text/event-stream", {}
        exec(compile(module, str(SOURCE), "exec"), env)
        iterator = await env["ask_stream"](
            "S_TEST",
            object(),
            SimpleNamespace(headers={"X-Data-Purpose": purpose} if purpose else {}),
            SimpleNamespace(ontology_id=16),
            object(),
            object(),
            session,
            opener,
        )
        output = []
        if disconnect:
            assert (await anext(iterator))[0] == "status"
            await iterator.aclose()
            assert set(cleaned) == {"connection", "work"}
            assert calls == ["rollback"]
        else:
            async for item in iterator:
                output.append(item)
                if item[0] in ("text", "done") and item[1].get("kind") != "failed":
                    assert session.committed
            assert calls[:2] == ["stored_events", "commit"]
            if fail_commit:
                assert calls == ["stored_events", "commit", "rollback"]
                assert [name for name, _ in output] == ["status", "error", "done"]
                assert output[-1][1]["kind"] == "failed"
            else:
                assert calls == ["stored_events", "commit"]
                assert output[-1] == ("done", {"kind": "answer"})
        assert contexts and all(context["active"] is False for context in contexts)

    await run_case()
    await run_case(fail_commit=True)
    await run_case(disconnect=True)
    await run_case(purpose=None)
    mcp = ast.parse((SOURCE.parent / "mcp_tools.py").read_text(encoding="utf-8"))
    forwarded = next(
        n.value
        for n in mcp.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "FORWARD_HEADERS" for t in n.targets)
    )
    assert "x-data-purpose" in ast.literal_eval(forwarded)


if __name__ == "__main__":
    asyncio.run(check())
    print("QA stream offline check passed: commit-before-answer, commit failure, disconnect cleanup.")
