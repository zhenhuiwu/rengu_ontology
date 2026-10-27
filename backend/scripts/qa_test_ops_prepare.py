"""One local action fixture for the explicitly isolated QA UI server; never a production seeder."""

import json
from datetime import date

from sqlalchemy import select


async def prepare_ops(session):
    assert session.get_bind().engine.url.database == "rengu_test_use_qa_20261027"
    from app.common.ontology import set_ontology_scope
    from app.core import clock
    from app.modules.action.models import ActionParam, ActionType, EditOp, OntologyEditRule, TypeStatus
    from app.modules.admin.models import SysRole, SysRoleMember, SysRoleScope, SysUser, UserStatus
    from app.modules.biz.models import Store, StoreStatus
    from app.modules.event.models import EventPayloadField, EventSource, EventType
    from app.modules.ontology.models import OntologyMember, OntologyRoleBinding
    from app.modules.static.models import ObjectStatus, ObjectType, Property

    await set_ontology_scope(session, 1)
    owner = await session.scalar(select(SysUser).where(SysUser.code == "U010001"))
    assert owner is not None and owner.status == UserStatus.active
    assert await session.scalar(
        select(OntologyMember.id).where(
            OntologyMember.ontology_id == 1,
            OntologyMember.user_id == owner.id,
            OntologyMember.role.in_(("owner", "editor")),
        )
    )
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    assert obj is not None and obj.status == ObjectStatus.published
    flag = next((p for p in obj.properties if p.api_name == "ops_ui_flag"), None)
    if flag is None:
        flag = Property(
            object_type_id=obj.id,
            api_name="ops_ui_flag",
            display_name="本地动作测试标记",
            data_type="boolean",
            prop_group="测试",
            source_column="store.is_test",
            backing_column="is_test",
            visibility="normal",
        )
        session.add(flag)
    else:
        assert flag.backing_column == "is_test" and not flag.is_sensitive and flag.visibility != "hidden"
    store = await session.scalar(select(Store).where(Store.code == "OPS-UI-LOCAL"))
    if store is None:
        store = Store(
            code="OPS-UI-LOCAL",
            store_no="OPS-UI-LOCAL",
            name="本地动作专用测试门店",
            city="测试城",
            region="OPS UI",
            big_region="OPS UI",
            format="测试",
            opened_at=date(2026, 1, 1),
            status=StoreStatus.open,
            is_test=False,
        )
        session.add(store)
    role = await session.scalar(select(SysRole).where(SysRole.code == "OPS-UI-LOCAL-ROLE"))
    if role is None:
        role = SysRole(
            code="OPS-UI-LOCAL-ROLE",
            name="本地动作UI测试角色",
            func={"action-center": "manage"},
            objs=[
                {
                    "api": obj.api_name,
                    "objType": obj.display_name,
                    "view": True,
                    "actions": ["ops_local"],
                    "export": False,
                }
            ],
            changed_at=clock.now(),
            changed_by=owner.code,
        )
        session.add(role)
        await session.flush()
        session.add(
            SysRoleScope(
                code="OPS-UI-LOCAL-SCOPE",
                role_id=role.id,
                seq=0,
                obj_type=obj.display_name,
                prop="大区",
                op="=",
                values=["OPS UI"],
            )
        )
    if not await session.scalar(
        select(SysRoleMember.id).where(SysRoleMember.role_id == role.id, SysRoleMember.user_id == owner.id)
    ):
        session.add(SysRoleMember(role_id=role.id, user_id=owner.id, seq=0))
    if not await session.scalar(
        select(OntologyRoleBinding.id).where(
            OntologyRoleBinding.ontology_id == 1, OntologyRoleBinding.role_id == role.id
        )
    ):
        session.add(OntologyRoleBinding(ontology_id=1, role_id=role.id))
    action = await session.scalar(select(ActionType).where(ActionType.code == "ops_local"))
    if action is None:
        action = ActionType(
            code="ops_local",
            api="OpsLocal",
            name="本地受控测试动作",
            grp="测试",
            status=TypeStatus.published,
            version="v1",
            target=obj.display_name,
            target_api=obj.api_name,
            target_kind="store",
            approval="免审",
        )
        action.params = [ActionParam(seq=0, api="enabled", name="测试标记", type="布尔", required=True)]
        action.edits = [
            OntologyEditRule(
                seq=0,
                op=EditOp.modify,
                obj="store",
                field=json.dumps({"values": {flag.api_name: {"param": "enabled"}}}),
            )
        ]
        session.add(action)
    else:
        assert action.api == "OpsLocal" and action.status == TypeStatus.published
        assert action.version == "v1" and action.sys_id is None and action.target_kind == "store"
    event = await session.scalar(select(EventType).where(EventType.api == "ActionExecuted"))
    if event is None:
        event = EventType(
            code="OPS-UI-ACTION-EXECUTED",
            name="本地测试动作完成",
            api="ActionExecuted",
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
    else:
        assert event.status == TypeStatus.published and event.object_api == "ActionRun"
    await session.flush()
    return {"typeId": "ops_local", "objKind": "store", "objId": "OPS-UI-LOCAL", "params": {"enabled": True}}
