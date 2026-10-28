"""No network/database service: execute real identity SQL against bounded in-memory rows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, literal, select

from app.common.auth import CurrentUser
from app.common.object_identity import identity_rows, primary_column
from app.common.pagination import PageParams
from app.common.sql_guard import GovernedTable
from app.core.errors import AppError, Forbidden
from app.modules.action import checker
from app.modules.dynamic import action_options, row_scope
from app.modules.integration import action_edits
from app.modules.static.models import ObjectStatus


async def test_primary_targets_legacy_ambiguity_and_runtime_visibility(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for sql in (
            "CREATE TABLE object_type (id INTEGER, code TEXT, status TEXT, pk_property TEXT)",
            "CREATE TABLE property (id INTEGER, object_type_id INTEGER, api_name TEXT, backing_column TEXT, "
            "source_column TEXT, is_pk BOOLEAN, is_sensitive BOOLEAN, visibility TEXT, transform TEXT)",
            "CREATE TABLE identity_order (id INTEGER, code TEXT, status TEXT)",
            "INSERT INTO object_type VALUES (1, 'order', 'published', 'id')",
            "INSERT INTO property VALUES (1, 1, 'id', NULL, 'id', 1, 0, 'normal', '')",
            "INSERT INTO identity_order VALUES (101, 'DUP', 'open'), (102, 'DUP', 'closed'), "
            "(103, 'OLD', 'open'), (104, '105', 'open'), (105, 'OTHER', 'open')",
        ):
            connection.exec_driver_sql(sql)
        table = Table(
            "identity_order",
            MetaData(),
            Column("id", Integer),
            Column("code", String),
            Column("status", String),
        )
        governed = GovernedTable(table, "order", {"order_id": table.c.code, "status": table.c.status})
        obj = SimpleNamespace(
            code="order",
            pk_property="id",
            properties=[
                SimpleNamespace(
                    api_name="id",
                    source_column="id",
                    backing_column=None,
                    is_pk=True,
                    is_sensitive=False,
                    visibility="normal",
                )
            ],
        )
        assert primary_column(obj, governed).compare(table.c.id)
        aliases = dict(connection.execute(identity_rows(governed)).all())
        assert aliases["101"] == 101 and aliases["102"] == 102 and aliases["OLD"] == 103
        assert "DUP" not in aliases and "105" not in aliases, (
            "duplicate codes and cross-namespace collisions must fail closed"
        )
        obj.properties[0].transform = "prefix"
        with pytest.raises(AppError):
            primary_column(obj, governed)
        connection.exec_driver_sql("UPDATE property SET transform='prefix'")
        assert "101" not in dict(connection.execute(identity_rows(governed)).all()), (
            "transformed primary keys cannot use raw columns"
        )
        obj.properties[0].transform = ""
        connection.exec_driver_sql("UPDATE property SET transform=''")

        class Session:
            def in_transaction(self):
                return True

            info = {"ontology_id": 2}

            async def execute(self, query):
                return connection.execute(query)

            async def scalar(self, query):
                return connection.scalar(query)

            async def scalars(self, query):
                return connection.execute(query).scalars()

        session = Session()
        user = CurrentUser(id="test", name="test", row_filters={"order": None}, ontology_id=2)
        assert (await action_edits.existing_row(session, user, obj, governed, "101"))["id"] == 101
        assert (await action_edits.existing_row(session, user, obj, governed, "OLD"))["id"] == 103
        for identity in ("DUP", "105", "missing"):
            with pytest.raises(Forbidden):
                await action_edits.existing_row(session, user, obj, governed, identity)
        restricted = CurrentUser(
            id="limited",
            name="limited",
            ontology_id=2,
            row_filters={"order": {"field": "status", "op": "=", "value": "open"}},
        )
        with pytest.raises(Forbidden):
            await action_edits.existing_row(session, restricted, obj, governed, "102")

        monkeypatch.setattr(action_options, "launch_definition", AsyncMock(return_value=({}, obj, governed)))
        catalog = SimpleNamespace(action_meta=lambda code: {})
        targets = await action_options.targets(
            session, restricted, catalog, "local", "DUP", PageParams(size=10)
        )
        assert targets.items == [{"id": "101", "label": "DUP（101）"}]
        monkeypatch.setattr(action_options, "resolve_user", AsyncMock(return_value=restricted))
        approval = SimpleNamespace(
            action_meta=lambda code: {"approvalRequired": True, "approverId": "limited"}
        )
        targets = await action_options.targets(session, user, approval, "local", "DUP", PageParams(size=1))
        assert targets.total == 1 and targets.items[0]["id"] == "101", (
            "targets must intersect the approver's row scope"
        )
        monkeypatch.setattr(action_edits, "object_binding", AsyncMock(return_value=(obj, governed)))
        permission = AsyncMock()
        monkeypatch.setattr(action_edits, "object_operation", permission)
        await action_edits.target_access(
            session, user, SimpleNamespace(obj_kind="order", obj_id="101"), {"targetKind": "order"}
        )
        permission.assert_awaited_once()
        monkeypatch.setattr(
            row_scope, "governed_tables", lambda ontology, _session: {"identity_order": governed}
        )
        monkeypatch.setattr(row_scope, "ensure_governed_tables", AsyncMock())
        query = await row_scope.scoped_instances(
            session, select(literal("visible")), restricted, literal("order"), literal("101")
        )
        assert connection.scalar(query) == "visible"
        query = await row_scope.scoped_instances(
            session, select(literal("visible")), restricted, literal("order"), literal("102")
        )
        assert connection.scalar(query) is None
        visible = await row_scope.visible_references(
            session, restricted, [("order", value) for value in ("101", "102", "OLD", "DUP")]
        )
        assert visible == {("order", "101"), ("order", "OLD")}
        obj.status, obj.api_name, obj.display_name = ObjectStatus.published, "Order", "订单"
        obj.properties[0].display_name = "编号"
        obj.properties.append(
            SimpleNamespace(
                api_name="status",
                display_name="状态",
                source_column="status",
                backing_column=None,
                is_sensitive=False,
                visibility="normal",
            )
        )
        monkeypatch.setattr(
            checker, "StaticRepo", lambda session: SimpleNamespace(object_type=AsyncMock(return_value=obj))
        )
        monkeypatch.setattr(
            checker, "governed_tables", lambda ontology, _session: {"identity_order": governed}
        )
        from app.common import sql_guard

        monkeypatch.setattr(sql_guard, "ensure_governed_tables", AsyncMock())
        bundles = [
            SimpleNamespace(
                role=SimpleNamespace(
                    code="role", name="role", default_deny=False, objs=[{"objType": "订单", "view": True}]
                ),
                scopes=[],
            )
        ]
        check = checker.DatabaseSubmissionChecker(session, user)
        refs = await check.context_references(
            {"targetKind": "order"}, {"objKind": "order", "objId": "101"}, bundles
        )
        assert refs[("上下文对象", "id")] == 101 and refs[("上下文对象", "状态")] == "open"
        refs = await check.context_references(
            {"targetKind": "order"}, {"objKind": "order", "objId": "DUP"}, bundles
        )
        assert ("上下文对象", "id") not in refs
    engine.dispose()


async def test_event_staging_uses_complete_published_definition_and_denies_missing_object_grant(monkeypatch):
    from app.contracts.actions import stage_event_action
    from app.db import dialects
    from app.modules.dynamic import catalog, ports, service

    definition = {
        "id": "local",
        "api": "local_api",
        "name": "本地动作",
        "targetKind": "order",
        "version": "v1",
    }
    load_definition = AsyncMock(return_value=definition)
    monkeypatch.setattr(checker, "definition", load_definition)
    obj = SimpleNamespace(code="order", api_name="Order", display_name="订单")
    monkeypatch.setattr(action_edits, "object_binding", AsyncMock(return_value=(obj, None)))
    check_row = AsyncMock()
    monkeypatch.setattr(action_edits, "existing_row", check_row)
    monkeypatch.setattr(dialects, "get_adapter", lambda session: SimpleNamespace(xact_lock=AsyncMock()))
    monkeypatch.setattr(catalog, "get_catalog", AsyncMock(return_value=SimpleNamespace()))
    for name in ("get_submitter", "get_emitter", "get_scheduler", "get_checker"):
        monkeypatch.setattr(ports, name, lambda *args: None)
    run = SimpleNamespace(code="ACT-test", status=SimpleNamespace(value="待确认"))
    staged = AsyncMock(return_value=(run, True))
    monkeypatch.setattr(
        service, "ActionService", lambda *args: SimpleNamespace(stage=staged, _log=AsyncMock())
    )
    permissions = [{"api": "Order", "view": True, "actions": ["local_api"]}]
    session = SimpleNamespace(
        info={"ontology_id": 2},
        scalar=AsyncMock(return_value=None),
        scalars=AsyncMock(side_effect=lambda query: [permissions]),
        flush=AsyncMock(),
    )
    user = CurrentUser(id="test", name="test", func={"action-center": "manage"}, ontology_id=2)
    result = await stage_event_action(
        session, user, action_type_code="local", event_id="event-1", params={}, obj_kind="order", obj_id="101"
    )
    assert result["id"] == "ACT-test" and result["status"] == "待确认"
    load_definition.assert_awaited_once()
    assert load_definition.await_args.args[3:] == ("published", None)
    assert staged.await_args.args[0].idempotency_key.startswith("evt-")
    check_row.assert_awaited_once()
    permissions.clear()
    with pytest.raises(Forbidden):
        await stage_event_action(
            session,
            user,
            action_type_code="local",
            event_id="event-2",
            params={},
            obj_kind="order",
            obj_id="101",
        )
    assert staged.await_count == 1, "a missing object operation grant must stop before staging"
