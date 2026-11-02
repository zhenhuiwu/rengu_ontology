"""Pure permission, serialization and route checks; no database, HTTP, credentials or rule execution."""

import asyncio
from inspect import getclosurevars
from types import SimpleNamespace

from fastapi import Request
from sqlalchemy.dialects import postgresql

from app.common.auth import CurrentUser, require_func
from app.core.config import api_prefix
from app.core.errors import AppError, Forbidden, NotFound
from app.modules.admin.api_keys import (
    clamp_permissions,
    metadata_permissions,
    require_api_scope,
    route_api_scopes,
    scope_permissions,
    token_page,
)
from app.modules.dynamic.rules_router import router
from app.modules.dynamic.rules_service import RuleService


def definition():
    # Generic inert fixture: expressions are never evaluated or compiled.
    return {
        "id": "metadata-example",
        "name": "Example",
        "version": 1,
        "status": "已发布",
        "owner": "Example",
        "core": False,
        "objKind": "example",
        "level": "中",
        "output": "通知",
        "desc": "Definition only",
        "conds": [
            {"expression": "example.amount", "op": ">", "value": "0", "httpAuth": {"secret": "hidden"}}
        ],
        "logic": "且",
        "expr": "example.amount > 0",
        "dedupe": "example.id",
        "cooldown": "1小时",
        "trigger": {
            "kind": "定时",
            "enabled": False,
            "cron": "0 8 * * *",
            "runAsUserId": "hidden",
            "httpAuth": {"secret": "hidden"},
        },
        "autoStage": {
            "enabled": False,
            "mapping": [{"param": "amount", "from": "example.amount", "secret": "hidden"}],
            "httpAuth": {"secret": "hidden"},
        },
        "weekly": 123,
        "draft": {"secret": "hidden"},
        "hits7d": 456,
        "stagedPending": 789,
        "httpAuth": {"secret": "hidden"},
    }


class SnapshotSession:
    def __init__(self, row):
        self.info = {"ontology_id": 17}
        self.row = row
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        assert statement.is_select
        compiled = statement.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        assert "ontology_release.ontology_id =" in sql
        assert 17 in compiled.params.values()
        assert "ORDER BY ontology_release.id DESC" in sql and "LIMIT" in sql
        assert {"rules", "metadata-example"} <= set(compiled.params.values())
        assert len(statement.selected_columns) == 2  # release version + one JSON rule, never full snapshot
        assert "rule_hit" not in sql and "action_run" not in sql and "rule_draft" not in sql
        return SimpleNamespace(first=lambda: self.row)


async def main():
    grants = {"rules": "manage", "users": "manage", "action-center": "manage"}
    carried = scope_permissions(["meta:read"], grants)
    assert carried == {"rules": "view"}
    assert metadata_permissions(CurrentUser(id="example", name="Example", func=grants)) == carried
    assert scope_permissions(["meta:read"], {"rules": "none"}) == {}
    assert clamp_permissions({}, grants) == {}  # old keys do not gain a rules grant
    assert clamp_permissions(carried, {"rules": "none"}) == {}  # role removal takes effect
    user = CurrentUser(id="example", name="Example", func=carried)
    assert await require_func("rules")(user) is user
    try:
        await require_func("rules", "manage")(user)
    except Forbidden:
        pass
    else:
        raise AssertionError("Metadata scope carried manage")

    routes = {route.path: route for route in router.routes}
    endpoint = routes["/rules/{rule_id}/definition"]
    assert endpoint.methods == {"GET"}
    assert route_api_scopes(endpoint) == {"meta:read"}
    assert any(
        getclosurevars(dependency.dependency).nonlocals == {"page_key": "rules", "level": "view"}
        for dependency in endpoint.dependencies
    )
    assert not route_api_scopes(routes["/rules/{rule_id}"])
    prefix = api_prefix("ontology")
    for path in (
        "/rules",
        "/rules/{rule_id}",
        "/rules/{rule_id}/hits",
        "/rules/{rule_id}/runs",
        "/rules/{rule_id}/versions",
        "/rules/{rule_id}:replay",
        "/rules/{rule_id}/draft",
    ):
        assert token_page("GET", prefix + path) is None
    assert token_page("POST", prefix + "/rules/{rule_id}/definition") is None
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    for scopes in ((), ("dynamic:read",), ("objects:read",)):
        request.state.api_key = SimpleNamespace(scopes=scopes)
        try:
            await require_api_scope("meta:read")(request, user)
        except Forbidden:
            pass
        else:
            raise AssertionError("Definition read accepted a key without meta:read")
    request.state.api_key = SimpleNamespace(scopes=("meta:read",))
    assert await require_api_scope("meta:read")(request, user) is user

    session = SnapshotSession(("v2026.10.1", definition()))
    result = await RuleService(session, user).published_metadata("metadata-example")
    out = result.model_dump(by_alias=True)
    assert session.calls == 1 and out["release"] == "v2026.10.1"
    assert out["definition"]["conds"][0]["value"] == "0"
    assert "hidden" not in str(out)
    assert not {"weekly", "draft", "hits7d", "stagedPending", "httpAuth"} & out["definition"].keys()
    for row in (
        None,
        ("v2026.10.1", None),
        ("v2026.10.1", {**definition(), "id": "another"}),
        ("v2026.10.1", {**definition(), "version": None}),
    ):
        try:
            await RuleService(SnapshotSession(row), user).published_metadata("metadata-example")
        except NotFound as exc:
            assert exc.details is None
        else:
            raise AssertionError("Missing/mismatched/unpublished snapshot was exposed")
    session = SnapshotSession(None)
    session.info.clear()
    try:
        await RuleService(session, user).published_metadata("metadata-example")
    except AppError as exc:
        assert exc.code == "ONTOLOGY_CONTEXT_REQUIRED" and session.calls == 0
    else:
        raise AssertionError("Missing workspace context accepted")
    print("Rule metadata read checks passed (no database, HTTP or rule execution).")


if __name__ == "__main__":
    asyncio.run(main())
