"""Offline assertions: definition reads defer binding reflection without granting object rows."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from app.common.auth import CurrentUser, bind_workspace, definition_only_request, row_filter
from app.core.errors import Forbidden


def request(path, *, query=b"", method="GET", headers=()):
    return Request({
        "type": "http", "method": method, "path": path, "query_string": query,
        "headers": [(b"x-ontology-id", b"8"), *headers],
        "route": SimpleNamespace(path=path, path_regex=re.compile("^" + re.escape(path) + "$")),
    })


class Session:
    def __init__(self, role="owner"):
        self.info = {}
        self.member = SimpleNamespace(role=role, user_id=1)

    async def get(self, *args):
        return SimpleNamespace(deleted_at=None)

    async def execute(self, *args):
        return SimpleNamespace(one_or_none=lambda: (self.member, "admin"))


async def main():
    prefix = "/api/ontology/v1"
    audited = (
        "/object-types", "/object-types/brief", "/object-types/{id}",
        "/object-types/{id}/properties", "/object-types/{id}/links", "/object-types/{id}/mappings",
        "/link-types", "/link-types/{id}", "/link-types/{id}/history", "/link-types/catalog",
        "/link-types/paths", "/ontology/graph", "/metrics", "/metrics/groups", "/metrics/{id}",
        "/metrics/{id}/versions", "/functions", "/functions/{code}", "/functions/{code}/versions",
        "/function-targets", "/action-types", "/action-types/{type_id}",
        "/action-types/{type_id}/params", "/action-types/{type_id}/submission-criteria",
        "/action-types/{type_id}/writeback", "/action-types/{type_id}/versions",
        "/event-types", "/event-types/{type_id}/payload-fields", "/event-types/{type_id}/versions",
    )
    admin = "/api/admin/v1"
    audited_admin = (
        "/me", "/governance/my-access", "/depts", "/users/options", "/tenants", "/roles",
        "/roles/state", "/roles/{role_id}", "/roles/object-permissions",
        "/settings/tenant", "/settings/models",
    )
    data = "/api/data/v1"
    audited_data = (
        "/datasources", "/datasources/meta", "/datasources/{ds_id}",
        "/datasources/{ds_id}/tables", "/datasources/{ds_id}/mappings",
        "/ext-systems", "/ext-systems/{sys_id}", "/ext-systems/{sys_id}/http-endpoints",
    )
    audited_paths = [*(prefix + path for path in audited), *(admin + path for path in audited_admin),
                     *(data + path for path in audited_data)]
    for path in audited_paths:
        assert definition_only_request(request(path)), path
        for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD"):
            assert not definition_only_request(request(path, method=method)), (path, method)
        for authorization in (b"Bearer test", b"", b"Basic test"):
            assert not definition_only_request(request(path, headers=[(b"authorization", authorization)]))
    for path in (
        "/meta/capabilities", "/exports/metricflow", "/ontology/lineage",
        "/ontology/overview", "/ontology/todos",
        "/object-types/{id}/meta", "/object-types/{id}/facets", "/object-types/{id}/usage",
        "/link-types/{id}/quality", "/link-types/{id}/usage", "/metrics/trial-readiness",
        "/metrics/{id}/trial-readiness", "/metrics/{id}/refs", "/action-execution-options",
        "/action-types/{type_id}/stats", "/action-types/{type_id}/stats/daily",
        "/action-types/{type_id}/used-by", "/event-types/{type_id}",
        "/event-types/{type_id}/subscribers", "/event-types/{type_id}/stats",
        "/event-types/{type_id}/events", "/event-types/{type_id}/delivery-exceptions",
        "/events/{event_id}", "/events/{event_id}/deliveries", "/event-deliveries/{delivery_id}",
        "/functions/{code}/unknown", "/action-types/{type_id}/unknown",
    ):
        assert not definition_only_request(request(prefix + path, query=b"readableOnly=true")), path
    for path in (
        "/users", "/roles/meta", "/roles/{role_id}/members", "/users/{user_id}/effective-access",
        "/me/row-scope", "/row-scope/org-columns", "/governance/policy/effective", "/audit-logs",
    ):
        assert not definition_only_request(request(admin + path)), path
    for path in (
        "/datasources/{ds_id}/schemas", "/datasources/{ds_id}/runs", "/quality-checks/meta",
        "/governed-bindings/{code}", "/external-read-bindings/{code}",
        "/ext-systems/{sys_id}/calls", "/mirror-scheduler/status",
    ):
        assert not definition_only_request(request(data + path)), path
    relative = request(prefix + "/object-types/OBJ/properties")
    relative.scope["route"] = SimpleNamespace(
        path="/object-types/{id}/properties",
        path_regex=re.compile(r"^/object-types/(?P<id>[^/]+)/properties$"),
    )
    assert definition_only_request(relative)
    assert not definition_only_request(
        request(prefix + "/change-requests", query=b"workflow=true&workflow=false")
    )
    assert definition_only_request(
        request(prefix + "/change-requests", query=b"workflow=false&workflow=true")
    )
    for path in ("/knowledge", "/knowledge/{id}", "/dimensions/{id}"):
        assert definition_only_request(request(prefix + path))
    assert definition_only_request(request(prefix + "/change-requests", query=b"workflow=true"))
    for path in (
        "/objects/{kind}/search", "/metrics/{id}:trial", "/change-requests", "/dimensions/{id}/usage"
    ):
        assert not definition_only_request(request(prefix + path))
    assert not definition_only_request(request(prefix + "/knowledge", method="POST"))
    assert not definition_only_request(
        request(prefix + "/knowledge", headers=[(b"authorization", b"Bearer test")])
    )
    assert not definition_only_request(request(prefix + "/change-requests", query=b"workflow=false"))
    from app.common.ontology import set_ontology_scope

    class ScopeSession:
        def __init__(self):
            self.info = {}
            self.execute = AsyncMock()

        def in_transaction(self):
            return True

        async def run_sync(self, fn):
            return fn(SimpleNamespace(connection=lambda: object()))

    with (
        patch("app.common.ontology._check_active") as active,
        patch("app.common.sql_guard.load_governed_tables", new_callable=AsyncMock) as bindings,
    ):
        scoped = ScopeSession()
        await set_ontology_scope(scoped, 8, load_data_bindings=False)
        assert scoped.info["ontology_id"] == 8 and active.call_count == 1
        assert "set_config" in str(scoped.execute.await_args.args[0])
        bindings.assert_not_awaited()
        await set_ontology_scope(scoped, 8)
        assert active.call_count == 2 and bindings.await_count == 1
    actor = CurrentUser(id="admin", name="admin")
    with (
        patch("app.common.ontology.set_ontology_scope", new_callable=AsyncMock) as scope,
        patch("app.modules.admin.permissions.object_row_access", new_callable=AsyncMock) as rows,
    ):
        rows.return_value = {"Device": None}
        metadata_actor = await bind_workspace(Session(), request(prefix + "/knowledge"), actor)
        assert metadata_actor.ontology_id == 8 and metadata_actor.ontology_role == "owner"
        assert metadata_actor.row_filters == {}
        assert await row_filter(metadata_actor, "Device") == {"deny": True}
        assert scope.await_args.kwargs == {"load_data_bindings": False}
        rows.assert_not_awaited()
        business_actor = await bind_workspace(Session(), request(prefix + "/objects/{kind}/search"), actor)
        assert business_actor.row_filters == {"Device": None}
        assert scope.await_args.kwargs == {"load_data_bindings": True}
        assert rows.await_count == 1
        try:
            await bind_workspace(Session(role=None), request(prefix + "/knowledge"), actor)
        except Forbidden:
            pass
        else:
            raise AssertionError("Definition reader bypassed workspace membership")
        assert scope.await_count == 2
        for path in audited_paths:
            metadata_actor = await bind_workspace(Session(), request(path), actor)
            assert metadata_actor.row_filters == {}
            assert await row_filter(metadata_actor, "Device") == {"deny": True}
            assert scope.await_args.kwargs == {"load_data_bindings": False}
        assert rows.await_count == 1
    # API-key identities keep their full context even without an Authorization header in this fake request.
    with (
        patch("app.common.ontology.set_ontology_scope", new_callable=AsyncMock) as scope,
        patch("app.modules.admin.api_keys.key_workspace_role", AsyncMock(return_value="service")),
        patch(
            "app.modules.admin.api_keys.key_row_filters",
            AsyncMock(return_value={"Device": {"deny": True}}),
        ),
    ):
        machine = CurrentUser(id="service", name="service", ontology_ids=(8,))
        machine_actor = await bind_workspace(Session(), request(prefix + "/functions"), machine)
        assert scope.await_args.kwargs == {}
        assert machine_actor.row_filters == {"Device": {"deny": True}}
    print("definition read context: PASS (no database or HTTP)")


if __name__ == "__main__":
    asyncio.run(main())
