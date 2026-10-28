"""Real PostgreSQL/RLS regressions for the use-area runtime; no external transports."""

import json
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import set_ontology_scope
from app.core import clock
from app.modules.action.models import ActionParam, ActionType, EditOp, OntologyEditRule, TypeStatus
from app.modules.admin.models import SysRole, SysRoleMember, SysRoleScope, SysUser, UserStatus
from app.modules.biz.models import Store, StoreStatus
from app.modules.dynamic.models import (
    ActionRun,
    ActionRunLog,
    ActionStatus,
    Alert,
    AlertLevel,
    AlertLog,
    AlertStatus,
    SourceType,
)
from app.modules.dynamic.rules_models import Rule, RuleOutput, RuleStatus
from app.modules.event.models import Event, EventPayloadField, EventSource, EventType
from app.modules.integration.models import (
    CallKind,
    CallResult,
    ExtSystem,
    ExtSystemStatus,
    ReconcileState,
    ReconcileTask,
    WritebackCall,
)
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding, OntologyWorkspace
from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectType, Property
from tests.use_ops_support import grant_ops_roles

API = "/api/ontology/v1"


def headers(code="U010001", ontology=1):
    return {"X-Demo-As": code, "X-Ontology-Id": str(ontology)}


def payload(**patch):
    return {
        "typeId": "ops_local",
        "source": {"type": "手动"},
        "objKind": "store",
        "objId": "OPS-S00",
        "objLabel": "client label is not authoritative",
        "params": json.dumps({"enabled": True}),
        "idempotencyKey": uuid4().hex,
        **patch,
    }


@pytest.fixture
async def runtime(session):
    await grant_ops_roles(session, {"U010001": "R-01"})
    scope_value = f"OPS-{uuid4().hex[:12]}"
    permission = {
        "api": "Store",
        "objType": "门店",
        "view": True,
        "actions": ["ops_local", "ops_cross"],
        "export": True,
    }
    admin = await session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    admin.objs = [permission]
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    if obj is None:
        obj = ObjectType(
            code="store",
            api_name="Store",
            display_name="门店",
            obj_group="测试",
            pk_property="ops_identity",
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=Lifecycle.active,
        )
        obj.properties = [
            Property(
                api_name="ops_identity",
                display_name="测试门店编号",
                data_type="string",
                prop_group="测试",
                is_pk=True,
                source_column="code",
                backing_column="code",
            ),
            Property(
                api_name="is_test",
                display_name="测试标记",
                data_type="boolean",
                prop_group="测试",
                source_column="store.is_test",
                backing_column="is_test",
            ),
        ]
        session.add(obj)
    else:
        assert obj.api_name == "Store"
        if not any(prop.api_name == "is_test" for prop in obj.properties):
            obj.properties.append(
                Property(
                    api_name="is_test",
                    display_name="测试标记",
                    data_type="boolean",
                    prop_group="测试",
                    source_column="store.is_test",
                    backing_column="is_test",
                )
            )
        assert any(prop.api_name == "is_test" and prop.data_type == "boolean" for prop in obj.properties)
    action = ActionType(
        code="ops_local",
        api="OpsLocal",
        name="本地受控动作",
        grp="测试",
        status=TypeStatus.published,
        version="v1",
        target="门店",
        target_api="Store",
        target_kind="store",
        approval="免审",
    )
    action.params = [ActionParam(seq=0, name="测试标记", api="enabled", type="布尔", required=True)]
    action.edits = [
        OntologyEditRule(
            seq=0, op=EditOp.modify, obj="store", field='{"values":{"is_test":{"param":"enabled"}}}'
        )
    ]
    cross = ActionType(
        code="ops_cross",
        api="OpsCross",
        name="跨实例动作",
        grp="测试",
        status=TypeStatus.published,
        version="v1",
        target="门店",
        target_api="Store",
        target_kind="store",
        approval="免审",
    )
    cross.params = [
        ActionParam(seq=0, name="测试标记", api="enabled", type="布尔", required=True),
        ActionParam(seq=1, name="另一门店", api="otherStore", type="对象引用 · 门店", required=True),
    ]
    cross.edits = [
        OntologyEditRule(
            seq=0,
            op=EditOp.modify,
            obj="store",
            field='{"target":{"param":"otherStore"},"values":{"is_test":{"param":"enabled"}}}',
        )
    ]
    session.add_all([action, cross])
    for i in range(13):
        session.add(
            Store(
                code=f"OPS-S{i:02}",
                store_no=f"OPS-N{i:02}",
                name=f"真实测试门店{i}",
                city="测试城",
                region="华东一区" if i < 12 else "华南一区",
                big_region=scope_value if i < 12 else f"{scope_value}-south",
                format="测试",
                opened_at=date(2026, 1, 1),
                status=StoreStatus.open,
            )
        )
    role = SysRole(
        code="OPS-LIMIT",
        name="ops limited",
        func={"alerts": "manage", "action-center": "manage"},
        objs=[permission],
        changed_at=clock.now(),
        changed_by="test",
    )
    session.add(role)
    people = [
        SysUser(code=code, name=name, status=status)
        for code, name, status in (
            ("OPS-LIMIT", "重名人员", UserStatus.active),
            ("OPS-SAME", "重名人员", UserStatus.active),
            ("OPS-DISABLED", "停用测试人员", UserStatus.disabled),
            ("OPS-VIEWER", "只读测试人员", UserStatus.active),
            ("OPS-NONMEMBER", "外部空间人员", UserStatus.active),
        )
    ]
    session.add_all(people)
    await session.flush()
    session.add(
        SysRoleScope(
            code="OPS-SCOPE",
            role_id=role.id,
            seq=0,
            obj_type="门店",
            prop="大区",
            op="=",
            values=[scope_value],
        )
    )
    session.add(OntologyRoleBinding(ontology_id=1, role_id=role.id))
    for person in people:
        session.add(SysRoleMember(role_id=role.id, user_id=person.id, seq=0))
        if person.code != "OPS-NONMEMBER":
            session.add(
                OntologyMember(
                    ontology_id=1,
                    user_id=person.id,
                    role="viewer" if person.code == "OPS-VIEWER" else "editor",
                )
            )
    for kind, name in (("ActionExecuted", "动作完成"), ("ActionOutcomeUnknown", "动作结果未知")):
        event = EventType(
            code=kind,
            api=kind,
            name=name,
            source=EventSource.action,
            status=TypeStatus.published,
            version="v1",
            retention_days=30,
            object_api="ActionRun",
            dedup_key_rule="执行记录 ID",
            dedup_window="24 小时",
        )
        event.payload = [EventPayloadField(seq=0, api="runId", name="执行记录", type="字符串", required=True)]
        session.add(event)
    await session.flush()
    for i in range(13):
        session.add(
            Alert(
                code=f"OPS-A{i:02}",
                rule_code="missing-real-rule",
                obj_kind="store",
                obj_id=f"OPS-S{i:02}",
                obj_label=f"门店{i}",
                value="1",
                threshold="0",
                level=AlertLevel.high,
                status=AlertStatus.open,
                triggered_at=clock.now(),
                data_cutoff=clock.now(),
            )
        )
        session.add(
            ActionRun(
                code=f"OPS-R{i:02}",
                action_type_code=action.code,
                action_name=action.name,
                type_version="v1",
                source_type=SourceType.manual,
                source_label="手动",
                initiator="王敏",
                confirmer="U010001",
                obj_kind="store",
                obj_id=f"OPS-S{i:02}",
                obj_label=f"门店{i}",
                params_text='{"enabled":true}',
                status=ActionStatus.staged,
                idem_key=f"ops-{i}",
                submitted_at=clock.now(),
                in_center=True,
            )
        )
    await session.flush()
    return session, action, cross


@pytest.fixture
async def alert_runtime(runtime):
    session, action, _ = runtime
    rule = Rule(
        code="OPS-RULE",
        name="本地真实告警规则",
        version=1,
        owner="U010001",
        status=RuleStatus.published,
        obj_kind="store",
        level=AlertLevel.high,
        output=RuleOutput.alert,
        conds=[],
        trigger={"kind": "event", "text": "测试事件触发"},
        action_type_id=action.id,
        auto_stage={"enabled": False},
    )
    session.add(rule)
    for alert in await session.scalars(select(Alert)):
        alert.rule_code = rule.code
    await session.flush()
    return session, action, rule


async def test_runtime_rows_counts_details_and_target_pagination(runtime, client):
    for path in ("alerts?status=all", "action-runs?tab=pending"):
        first = await client.get(f"{API}/{path}", headers=headers("OPS-LIMIT"))
        assert first.status_code == 200, first.text
        assert first.json()["total"] == 12 and len(first.json()["items"]) == 10
        last = await client.get(f"{API}/{path}&page=99", headers=headers("OPS-LIMIT"))
        assert last.json()["page"] == 2 and len(last.json()["items"]) == 2
        assert not {x["id"] for x in first.json()["items"]} & {x["id"] for x in last.json()["items"]}
    for path in ("alerts/OPS-A12", "action-runs/OPS-R12"):
        assert (await client.get(f"{API}/{path}", headers=headers("OPS-LIMIT"))).status_code == 404
    for page, size in ((1, 10), (2, 2)):
        result = await client.get(
            f"{API}/actions/ops_local/targets", params={"page": page}, headers=headers("OPS-LIMIT")
        )
        assert result.status_code == 200, result.text
        assert result.json()["total"] == 12 and len(result.json()["items"]) == size
    all_alerts = (await client.get(f"{API}/alerts?status=all", headers=headers("OPS-LIMIT"))).json()
    assert all_alerts["counts"] == {"open": 12, "doing": 0, "closed": 0, "all": 12}


async def test_runtime_visibility_queries_do_not_grow_per_row(runtime, client, engine):
    session = runtime[0]
    for i, alert in enumerate(await session.scalars(select(Alert).order_by(Alert.code))):
        alert.dims = [{"objKind": "store", "id": f"OPS-S{i:02}", "label": "真实维度"}]
    await session.flush()
    statements = []

    def capture(_conn, _cursor, sql, _params, _ctx, _many):
        if sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        for path in ("alerts?status=all", "action-runs?tab=pending"):
            counts = []
            for size in (5, 13):
                statements.clear()
                response = await client.get(f"{API}/{path}&size={size}", headers=headers())
                assert response.status_code == 200, response.text
                assert len(response.json()["items"]) == size
                counts.append(len(statements))
            print(f"{path} SELECTs for 5/13 rows: {counts}")
            assert counts[1] <= counts[0] + 2, f"per-row visibility queries: {path} {counts}"
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


async def test_transfer_stable_id_checks_instance_membership_and_ambiguous_name(runtime, client):
    session, _, _ = runtime
    result = await client.get(f"{API}/people?alertId=OPS-A00", headers=headers())
    assert result.status_code == 200, result.text
    ids = {person["id"] for person in result.json()}
    assert {"OPS-LIMIT", "OPS-SAME", "U010001"} <= ids
    assert not ids & {"OPS-VIEWER", "OPS-DISABLED", "OPS-NONMEMBER"}
    south = (await client.get(f"{API}/people?alertId=OPS-A12", headers=headers())).json()
    assert "OPS-LIMIT" not in {p["id"] for p in south}
    assert (
        await client.post(f"{API}/alerts/OPS-A00:transfer", json={"to": "重名人员"}, headers=headers())
    ).status_code == 422
    changed = await client.post(f"{API}/alerts/OPS-A00:transfer", json={"to": "OPS-LIMIT"}, headers=headers())
    assert changed.status_code == 200, changed.text
    assert changed.json()["assigneeId"] == "OPS-LIMIT" and changed.json()["assignee"] == "重名人员"
    await session.refresh(await session.scalar(select(Alert).where(Alert.code == "OPS-A00")))
    assert await session.scalar(select(Alert.assignee).where(Alert.code == "OPS-A00")) == "OPS-LIMIT"
    assert (
        await client.post(
            f"{API}/alerts/OPS-A00:close",
            json={"as": "已解决", "reason": "test"},
            headers=headers("OPS-SAME"),
        )
    ).status_code == 403
    assert (
        await client.post(f"{API}/alerts/OPS-A12:transfer", json={"to": "OPS-LIMIT"}, headers=headers())
    ).status_code == 422


async def test_batch_close_skips_inflight_and_missing_id_is_atomic(runtime, client):
    session, _, _ = runtime
    await client.post(f"{API}/alerts:batch-claim", json={"ids": ["OPS-A00", "OPS-A01"]}, headers=headers())
    alert = await session.scalar(select(Alert).where(Alert.code == "OPS-A00"))
    alert.action_run_id = await session.scalar(select(ActionRun.id).where(ActionRun.code == "OPS-R00"))
    await session.flush()
    failed = await client.post(
        f"{API}/alerts:batch-close",
        json={"ids": ["OPS-A01", "NOPE"], "as": "已解决", "reason": "test"},
        headers=headers(),
    )
    assert failed.status_code == 404
    assert await session.scalar(select(Alert.status).where(Alert.code == "OPS-A01")) == AlertStatus.processing
    result = await client.post(
        f"{API}/alerts:batch-close",
        json={"ids": ["OPS-A00", "OPS-A01"], "as": "已解决", "reason": "test"},
        headers=headers(),
    )
    assert result.json() == {"closed": 1, "ids": ["OPS-A01"]}
    assert await session.scalar(select(Alert.status).where(Alert.code == "OPS-A00")) == AlertStatus.processing


async def test_validate_is_no_write_and_nullable_system_join_is_valid_postgresql(runtime, client):
    session, _, _ = runtime
    models = (ActionRun, ActionRunLog, AlertLog, WritebackCall, Event)
    before = [await session.scalar(select(func.count()).select_from(model)) for model in models]
    invalid = await client.post(
        f"{API}/actions/ops_local:validate",
        json={"objKind": "store", "objId": "OPS-S00", "params": {}},
        headers=headers(),
    )
    assert invalid.status_code == 422, invalid.text
    valid = await client.post(
        f"{API}/actions/ops_local:validate",
        json={"objKind": "store", "objId": "OPS-S00", "params": {"enabled": True}},
        headers=headers(),
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["passed"] is True
    assert before == [await session.scalar(select(func.count()).select_from(model)) for model in models]
    assert await session.scalar(select(Store.is_test).where(Store.code == "OPS-S00")) is False


async def test_stage_uuid_replay_conflict_and_provenance(runtime, client):
    session, _, _ = runtime
    body = payload()
    first = await client.post(f"{API}/action-runs:stage", json=body, headers=headers())
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["status"] == "待确认" and result["confirmer"] == "王敏" and result["objLabel"] == "OPS-S00"
    again = await client.post(f"{API}/action-runs:stage", json=body, headers=headers())
    assert again.status_code == 200 and again.json()["id"] == result["id"]
    conflict = await client.post(
        f"{API}/action-runs:stage", json={**body, "params": '{"enabled":false}'}, headers=headers()
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    for source in (
        {"type": "问数", "session": "private"},
        {"type": "规则", "ruleId": "fake"},
        {"type": "手动", "confirmer": "OPS-LIMIT"},
    ):
        denied = await client.post(
            f"{API}/action-runs:stage", json={**body, "source": source}, headers=headers()
        )
        assert denied.status_code == 422
    assert await session.scalar(select(func.count()).select_from(WritebackCall)) == 0


async def test_local_confirm_uses_real_edits_and_event_once(runtime, client):
    session, _, _ = runtime
    staged = await client.post(f"{API}/action-runs:stage", json=payload(), headers=headers())
    assert staged.status_code == 201, staged.text
    code = staged.json()["id"]
    result = await client.post(f"{API}/action-runs/{code}:confirm", headers=headers())
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "成功" and result.json()["code"] == "INTERNAL"
    assert result.json()["log"][-1]["text"] == "动作执行成功"
    assert all("None" not in log["text"] and "外部单号" not in log["text"] for log in result.json()["log"])
    assert await session.scalar(select(Store.is_test).where(Store.code == "OPS-S00")) is True
    assert await session.scalar(select(func.count()).select_from(Event)) == 1
    assert (await client.post(f"{API}/action-runs/{code}:confirm", headers=headers())).status_code == 409
    assert await session.scalar(select(func.count()).select_from(Event)) == 1


async def test_cross_object_parameters_logs_and_mutations_fail_closed(runtime, client):
    session, _, cross = runtime
    record = await session.scalar(select(ActionRun).where(ActionRun.code == "OPS-R00"))
    record.action_type_code, record.action_name = cross.code, cross.name
    record.params_text = '{"enabled":true,"otherStore":"OPS-S12"}'
    record.confirmer = "OPS-LIMIT"
    session.add(ActionRunLog(action_run_id=record.id, seq=1, at=clock.now(), text="private OPS-S12"))
    await session.flush()
    response = await client.get(f"{API}/action-runs/{record.code}", headers=headers("OPS-LIMIT"))
    assert response.status_code == 200, response.text
    assert response.json()["params"] is None and response.json()["paramsUnavailable"]
    assert not response.json()["log"] and "OPS-S12" not in response.text
    denied = await client.post(f"{API}/action-runs/{record.code}:confirm", headers=headers("OPS-LIMIT"))
    assert denied.status_code == 403 and denied.json()["code"] == "ACTION_REFERENCE_UNAVAILABLE"


async def test_workspace_empty_and_explicit_routing_does_not_fallback(runtime, client, conn):
    session, _, _ = runtime
    workspace = OntologyWorkspace(code="ops-second", name="ops second")
    session.add(workspace)
    await session.flush()
    person_id = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    session.add(OntologyMember(ontology_id=workspace.id, user_id=person_id, role="owner"))
    await session.flush()
    from app.db.session import get_session
    from app.main import app

    original = app.dependency_overrides[get_session]

    async def other_session():
        async with AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as other:
            await set_ontology_scope(other, workspace.id)
            yield other
            await other.commit()

    app.dependency_overrides[get_session] = other_session
    try:
        for path in ("alerts?status=all", "action-runs?tab=pending"):
            response = await client.get(f"{API}/{path}", headers=headers(ontology=workspace.id))
            assert response.status_code == 200 and response.json()["total"] == 0
        options = await client.get(f"{API}/actions/available", headers=headers(ontology=workspace.id))
        assert options.status_code == 200 and options.json() == []
    finally:
        app.dependency_overrides[get_session] = original
        await set_ontology_scope(session, 1)
    for value in ("invalid", "0", "999999"):
        assert (
            await client.get(f"{API}/alerts", headers={"X-Demo-As": "U010001", "X-Ontology-Id": value})
        ).status_code in (422, 404)


@pytest.mark.parametrize("phase", ["ACTION_DISPATCH_QUEUED", "ACTION_DISPATCH_CLAIMED"])
async def test_queue_phases_are_truthful_and_reject_manual_outcome(runtime, client, phase):
    session, action, _ = runtime
    system = ExtSystem(
        code="OPS-EXT", name="ops test gateway", timeout_ms=1000, status=ExtSystemStatus.ok, env="test"
    )
    session.add(system)
    await session.flush()
    action.sys_id = system.id
    run = await session.scalar(select(ActionRun).where(ActionRun.code == "OPS-R00"))
    run.status, run.sys_code = ActionStatus.unknown, system.code
    session.add(
        WritebackCall(
            code="OPS-CALL",
            sys_id=system.id,
            action_run_id=run.id,
            kind=CallKind.write,
            at=clock.now(),
            op_name=run.action_name,
            target_label=run.obj_id,
            method="POST",
            path="/not-called",
            idem_key=run.idem_key,
            elapsed_ms=0,
            result=CallResult.unknown,
            attempt=0,
            actor="U010001",
            msg=phase,
        )
    )
    session.add(
        ReconcileTask(
            action_run_id=run.id,
            sys_id=system.id,
            idem_key=run.idem_key,
            state=ReconcileState.querying,
            next_at=clock.now(),
            manual=False,
        )
    )
    await session.flush()
    response = await client.get(f"{API}/action-runs/{run.code}", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["dispatchPhase"] == ("queued" if "QUEUED" in phase else "claimed")
    assert response.json()["can"]["reconcile"] is False
    denied = await client.post(
        f"{API}/action-runs/{run.code}:reconcile",
        json={"result": "外部未执行", "note": "test"},
        headers=headers(),
    )
    assert denied.status_code == 409 and denied.json()["code"] == "ACTION_STILL_QUEUED", denied.text


@pytest.fixture
async def published_store_runtime(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections
    from app.seeds.loader import run_seeds

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
    return await runtime.__wrapped__(session)


async def test_published_store_primary_targets_keep_row_and_workspace_limits(
    published_store_runtime, client, conn
):
    session = published_store_runtime[0]
    own_scope = await session.scalar(select(Store.big_region).where(Store.code == "OPS-S00"))
    other = OntologyWorkspace(code="ops-primary-other", name="separate primary workspace")
    session.add(other)
    await session.flush()
    async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint") as foreign:
        await set_ontology_scope(foreign, other.id)
        foreign.add(
            Store(
                code="FOREIGN-ONLY",
                store_no="FOREIGN-N",
                name="不可见门店",
                city="测试城",
                region="华东一区",
                big_region=own_scope,
                format="测试",
                opened_at=date(2026, 1, 1),
                status=StoreStatus.open,
            )
        )
        await foreign.commit()
    await set_ontology_scope(session, 1)
    response = await client.get(
        f"{API}/actions/ops_local/targets", params={"size": 200}, headers=headers("OPS-LIMIT")
    )
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()["items"]} == {f"OPS-N{i:02}" for i in range(12)}
    assert response.json()["total"] == 12
    assert "OPS-N12" not in response.text and "FOREIGN" not in response.text
    denied = await client.get(f"{API}/actions/ops_local/targets", headers=headers("OPS-NONMEMBER"))
    assert denied.status_code == 403, denied.text


def test_store_primary_mapping_still_rejects_protected_and_undeclared_columns():
    from types import SimpleNamespace

    from app.common.object_identity import property_column
    from app.modules.biz.governance import STORE_GOV

    prop = SimpleNamespace(
        backing_column="store_no",
        source_column="store_id",
        is_sensitive=False,
        visibility="prominent",
        transform="",
    )
    assert property_column(prop, STORE_GOV) is Store.__table__.c.store_no
    for changes in (
        {"is_sensitive": True},
        {"visibility": "hidden"},
        {"transform": "lower(store_no)"},
        {"backing_column": "manager"},
    ):
        guarded = SimpleNamespace(**(vars(prop) | changes))
        assert property_column(guarded, STORE_GOV) is None
