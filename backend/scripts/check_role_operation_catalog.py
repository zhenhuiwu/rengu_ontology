"""Read-only role catalog binding reuse, without database, HTTP or execution."""

import asyncio
from collections import Counter
from types import SimpleNamespace as Namespace
from unittest.mock import AsyncMock, patch

from app.core.errors import AppError
from app.modules.action import execution_options as options
from app.modules.admin.roles import load_perm_objects


class Session:
    def __init__(self):
        self.transaction = object()
        self.info = {"ontology_id": 16, "governance_user": object()}
        self.sync_session = Namespace(get_transaction=lambda: self.transaction)
        self.events = Counter()

    async def scalar(self, statement):
        params = statement.compile().params
        code = params["code_1"]
        self.events[code] += 1
        return None if code == "missing" else Namespace(obj_kind="EventObject")


def definition():
    return {
        "targetKind": "Task",
        "writeback": {
            "edits": [
                {"op": "关联", "obj": "task_asset"},
                {"op": "更新", "obj": "Extra"},
                {"op": "更新", "obj": "bad"},
                {"op": "更新", "obj": "Task"},
            ],
            "effects": [{"kind": "事件", "target": "event"}, {"kind": "事件", "target": "missing"}],
        },
    }


async def main():
    calls = Counter()

    async def object_binding(session, identity):
        calls[("object", identity)] += 1
        if identity == "bad":
            raise AppError("ambiguous or unauthorized binding")
        return Namespace(
            code=identity, api_name=identity + "API", scope=session.info["ontology_id"]
        ), object()

    async def relation_binding(session, identity):
        calls[("relation", identity)] += 1
        if identity == "bad":
            raise AppError("invalid FK")
        src, _ = await object_binding(session, "Task")
        dst, _ = await object_binding(session, "Asset")
        return Namespace(), src, Namespace(), dst, Namespace(), Namespace()

    with (
        patch.object(options, "object_binding", side_effect=object_binding),
        patch.object(options, "relation_binding", side_effect=relation_binding),
    ):
        session = Session()
        # Relation binding itself retains its original endpoint reads, refresh and FK validation.
        default = [await options.action_operation_objects(session, definition()) for _ in range(2)]
        assert calls[("object", "Extra")] == 2 and calls[("relation", "task_asset")] == 2
        assert session.events == {"event": 2, "missing": 2}
        calls.clear()
        session.events.clear()
        cache = {}
        cached = [
            await options.action_operation_objects(session, definition(), cache=cache) for _ in range(2)
        ]
        assert [[o.code for o in rows] for rows in cached] == [[o.code for o in rows] for rows in default]
        assert calls[("object", "Extra")] == calls[("object", "bad")] == 1
        assert calls[("relation", "task_asset")] == 1
        assert session.events == {"event": 1, "missing": 1}
        assert all(o.code != "bad" for o in cached[0])
        bad = {"writeback": {"edits": [{"op": "关联", "obj": "bad"}]}}
        assert await options.action_operation_objects(session, bad, cache=cache) == []
        assert await options.action_operation_objects(session, bad, cache=cache) == []
        assert calls[("relation", "bad")] == 1
        # Each context boundary and a new owned dictionary requires a fresh validated resolution.
        simple = {"targetKind": "Extra"}
        for boundary in ("new_call", "ontology", "transaction", "actor", "session"):
            before = calls[("object", "Extra")]
            if boundary == "new_call":
                cache = {}
            if boundary == "ontology":
                session.info["ontology_id"] = 17
            if boundary == "transaction":
                session.transaction = object()
            if boundary == "actor":
                session.info["governance_user"] = object()
            if boundary == "session":
                session = Session()
            rows = await options.action_operation_objects(session, simple, cache=cache)
            assert calls[("object", "Extra")] == before + 1, boundary
            assert rows[0].scope == session.info["ontology_id"]

        # Execute the actual role catalog twice: its cache is owned by one invocation alone.
        actions = [
            Namespace(code=f"A{i}", name=f"Action {i}", api=f"a{i}", target_api="TaskAPI") for i in range(2)
        ]
        session = Session()
        session.execute = AsyncMock(
            return_value=Namespace(all=lambda: [("Task", "TaskAPI"), ("Extra", "ExtraAPI")])
        )
        session.scalars = AsyncMock(return_value=actions)
        with patch("app.modules.action.sections.action_def", return_value={"targetKind": "Task"}):
            calls.clear()
            first = await load_perm_objects(session)
            assert calls[("object", "Task")] == 1, "two actions on one target should bind it once"
            assert first[0]["actions"] == ["Action 0", "Action 1"]
            assert [ref["id"] for ref in first[0]["actionRefs"]] == ["A0", "A1"]
            second = await load_perm_objects(session)
            assert first == second and calls[("object", "Task")] == 2
    print("role operation catalog: PASS (local reuse, failures, boundaries and uncached runtime; no DB/HTTP)")


if __name__ == "__main__":
    asyncio.run(main())
