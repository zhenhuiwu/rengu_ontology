"""Current workspace scope binding checks; retained without executing backend code."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import Column, MetaData, String, Table

from app.common.auth import CurrentUser
from app.common.row_filter import DENY_ALL
from app.common.sql_guard import GovernedTable, RoleAccess, effective_expr
from app.modules.admin.permissions import role_row_access


async def test_role_scopes_use_only_current_governed_mapping():
    table = Table("external_object", MetaData(), Column("SOURCE_REGION", String))
    mapping = GovernedTable(
        table, "ExternalObject", {"SOURCE_REGION": table.c.SOURCE_REGION},
        field_of_prop={"大区": "SOURCE_REGION"}, projection=frozenset({"SOURCE_REGION"}),
    )
    scope = {"prop": "大区", "op": "=", "values": ["华东"]}
    role = SimpleNamespace(
        id=7, objs=[{"api": "external_object", "view": True}], default_deny=True,
    )
    stored_scope = SimpleNamespace(
        role_id=7, obj_type="外部对象", object_type_id=3, object_type_code="ExternalObject", **scope,
    )
    obj = SimpleNamespace(id=3, code="ExternalObject", api_name="external_object", display_name="外部对象")
    session = SimpleNamespace(info={"ontology_id": 42}, execute=AsyncMock(side_effect=[[stored_scope], [obj]]))
    registry = {mapping.name: mapping}
    with (
        patch("app.common.sql_guard.ensure_governed_tables", new_callable=AsyncMock) as ensure,
        patch("app.common.sql_guard.governed_tables", return_value=registry) as current,
    ):
        result = await role_row_access(session, [role])
    ensure.assert_awaited_once_with(session)
    current.assert_called_once_with(42, session)
    assert result == {"ExternalObject": {"field": "SOURCE_REGION", "op": "=", "value": "华东"}}

    grants = [RoleAccess(True, scopes=(scope,))]
    assert effective_expr("ExternalObject", grants, governed={}) == DENY_ALL
    assert effective_expr("ExternalObject", grants, governed={"other": GovernedTable(table, "Other", {})}) == DENY_ALL
    assert effective_expr("ExternalObject", [RoleAccess(False)], governed=registry) == DENY_ALL
    assert effective_expr("ExternalObject", [RoleAccess(True, scopes=({**scope, "prop": "未映射"},))], governed=registry) == DENY_ALL


async def test_action_context_never_recompiles_a_denied_identity_as_unrestricted():
    from app.modules.action.checker import DatabaseSubmissionChecker

    session = SimpleNamespace(info={"ontology_id": 42}, execute=AsyncMock())
    user = CurrentUser(id="viewer", name="viewer", ontology_id=42, row_filters={"ExternalObject": DENY_ALL})
    mapping = GovernedTable(Table("external_object", MetaData()), "ExternalObject", {})
    obj = SimpleNamespace(status=SimpleNamespace(value="已发布"))
    bundle = SimpleNamespace(
        role=SimpleNamespace(code="role", name="role", objs=[{"objType": "外部对象", "view": True}], default_deny=False),
        scopes=[],
    )
    with (
        patch("app.common.sql_guard.ensure_governed_tables", new_callable=AsyncMock),
        patch("app.modules.action.checker.governed_tables", return_value={mapping.name: mapping}),
        patch("app.modules.action.checker.StaticRepo", return_value=SimpleNamespace(object_type=AsyncMock(return_value=obj))),
    ):
        refs = await DatabaseSubmissionChecker(session, user).context_references(
            {"targetKind": "ExternalObject"}, {"objKind": "ExternalObject", "objId": "target"}, [bundle],
        )
    assert not any(key[0] == "上下文对象" for key in refs)
    session.execute.assert_not_awaited()
