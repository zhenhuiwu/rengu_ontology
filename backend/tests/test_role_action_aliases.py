"""Bounded role action validation; no database, migrations, network, or real authorization writes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.common.auth import CurrentUser
from app.core.errors import VersionConflict
from app.modules.action import execution_options, sections
from app.modules.action.models import TypeStatus
from app.modules.admin import roles
from app.modules.admin.schemas import PermObjectOut


async def test_role_action_aliases_are_bound_to_actual_workspace_objects(monkeypatch):
    metadata = [
        {"name": "订单", "api": "SalesOrder", "actions": ["修正订单", "旧种子未发布动作"]},
        {"name": "商品", "api": "Product", "actions": ["商品调价"]},
    ]
    action = SimpleNamespace(code="AT-REAL", api="fix_order", name="修正订单", target_api="SalesOrder")
    price = SimpleNamespace(code="AT-PRICE", api="reprice", name="商品调价", target_api="Product")
    session = SimpleNamespace(
        info={"ontology_id": 2},
        scalars=AsyncMock(return_value=[action, price]),
        scalar=AsyncMock(return_value=1),
        refresh=AsyncMock(),
    )
    service = roles.RoleService(session)
    role = SimpleNamespace(name="测试角色", objs=[], id=1, lock_version=1)
    service._editable = AsyncMock(return_value=role)
    service._touch = lambda *_: None
    service.repo.update = AsyncMock()
    service._audit = AsyncMock()
    service.get = AsyncMock(return_value=role)
    monkeypatch.setattr(roles, "load_perm_objects", AsyncMock(return_value=metadata))
    monkeypatch.setattr(sections, "action_def", lambda value: {"id": value.code})
    monkeypatch.setattr(execution_options, "action_operation_objects", AsyncMock(return_value=[]))
    user = CurrentUser(id="test", name="Test", ontology_id=2)

    async def save(identities, obj="订单", api=None, lock_version=1, view=True):
        return await service.set_permissions(
            "R-TEST",
            fn={},
            objs=[
                {
                    "objType": obj,
                    "api": api or ("SalesOrder" if obj == "订单" else "Product"),
                    "view": view,
                    "export": False,
                    "actions": identities,
                }
            ],
            lock_version=lock_version,
            user=user,
            ip="127.0.0.1",
        )

    for identity in (action.code, action.api, action.name):
        await save([identity])
        saved = service.repo.update.call_args.args[1]["objs"]
        assert saved[0]["actions"] == [identity], "preserve the existing authorized spelling"

    query = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "action_type.ontology_id =" in str(query)
    assert "action_type.retired_release_id IS NULL" in str(query)
    assert 2 in query.params.values()
    assert any(
        set(value) == {TypeStatus.published, TypeStatus.changing}
        for value in query.params.values()
        if isinstance(value, list)
    )

    for identity in (
        "unknown",
        "AT-OTHER-WORKSPACE",
        "AT-DRAFT",
        "旧种子未发布动作",
        price.code,
        price.api,
        price.name,
    ):
        service.repo.update.reset_mock()
        with pytest.raises(roles.RoleValidation):
            await save([identity])
        service.repo.update.assert_not_awaited()

    execution_options.action_operation_objects.return_value = [SimpleNamespace(api_name="Product")]
    await save([action.api], obj="商品")
    saved = service.repo.update.call_args.args[1]["objs"]
    assert next(obj for obj in saved if obj["api"] == "Product")["actions"] == [action.api]
    execution_options.action_operation_objects.return_value = []
    stale = "retired-order-value"
    role.objs = [{"objType": "订单", "api": "SalesOrder", "view": True, "export": False, "actions": [stale]}]
    original = role.objs
    await save([stale, action.code])
    assert service.repo.update.call_args.args[1]["objs"][0]["actions"] == [stale, action.code]
    await save([])
    assert service.repo.update.call_args.args[1]["objs"][0]["actions"] == []
    role.objs = service.repo.update.call_args.args[1]["objs"]
    with pytest.raises(roles.RoleValidation):
        await save([stale])
    role.objs = original
    for identities, kwargs in [
        ([stale], {"obj": "商品"}),
        ([stale + "-alias"], {}),
        ([stale], {"api": "Product"}),
        ([stale], {"lock_version": None}),
    ]:
        with pytest.raises(roles.RoleValidation):
            await save(identities, **kwargs)
    session.scalar.return_value = None
    with pytest.raises(roles.RoleValidation):
        await save([stale])
    session.scalar.return_value = 1
    original_user = user
    user = CurrentUser(id="test", name="Test", ontology_id=3)
    with pytest.raises(roles.RoleValidation):
        await save([stale])
    user = original_user
    role.objs[0]["view"] = False
    await save([stale], view=False)
    assert service.repo.update.call_args.args[1]["objs"][0]["actions"] == [stale]
    await save([stale, action.code], view=False)
    assert service.repo.update.call_args.args[1]["objs"][0]["actions"] == [stale], "no new latent grants"
    await save([], view=False)
    assert service.repo.update.call_args.args[1]["objs"][0]["actions"] == []
    with pytest.raises(roles.RoleValidation):
        await save([stale])
    role.objs[0]["view"] = True
    role.lock_version = 2
    service.repo.update.reset_mock()
    with pytest.raises(VersionConflict):
        await save([stale])
    service.repo.update.assert_not_awaited()
    assert session.refresh.call_args.kwargs == {"with_for_update": True}


async def test_role_metadata_exposes_action_aliases_per_actual_object(monkeypatch):
    action = SimpleNamespace(code="AT-REAL", api="fix_order", name="修正订单", target_api="SalesOrder")
    session = SimpleNamespace(
        info={"ontology_id": 2},
        execute=AsyncMock(
            return_value=SimpleNamespace(all=lambda: [("订单", "SalesOrder"), ("商品", "Product")])
        ),
        scalars=AsyncMock(return_value=[action]),
    )
    monkeypatch.setattr(sections, "action_def", lambda value: {"id": value.code})
    monkeypatch.setattr(execution_options, "action_operation_objects", AsyncMock(return_value=[]))
    metadata = await roles.load_perm_objects(session)
    order = PermObjectOut.model_validate(metadata[0]).model_dump(by_alias=True)
    assert order["actionRefs"] == [{"id": action.code, "api": action.api, "name": action.name}]
    assert metadata[1]["actionRefs"] == []
    query = session.scalars.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "action_type.ontology_id =" in str(query) and 2 in query.params.values()
    assert "action_type.retired_release_id IS NULL" in str(query)
