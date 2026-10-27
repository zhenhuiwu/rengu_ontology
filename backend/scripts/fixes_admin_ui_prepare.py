"""Append only owned UICHECK-FIX fixtures in the previously authorized QA UI database."""

import hashlib
import json
import secrets
from datetime import date
from types import SimpleNamespace

from sqlalchemy import select

DATABASE = "rengu_test_use_qa_20261027"
PREFIX = "UICHECK-FIX"


async def prepare(session):
    assert session.get_bind().engine.url.database == DATABASE
    from app.common.auth import resolve_user
    from app.common.ontology import require_ontology_id, set_ontology_scope
    from app.common.outbox import OutboxEvent, emit
    from app.core import clock
    from app.modules.action.models import (
        ActionParam,
        ActionType,
        ActionTypeVersion,
        EditOp,
        EffectKind,
        OntologyEditRule,
        SideEffect,
        TypeStatus,
    )
    from app.modules.action.sections import action_def
    from app.modules.admin.models import ApiKey, SysRole, SysRoleMember, SysRoleScope, SysUser
    from app.modules.biz.models import Store, StoreStatus
    from app.modules.dynamic.models import ActionRun, ActionStatus, SourceType
    from app.modules.functions.models import FnVersion, Function
    from app.modules.integration.action_effects import TOPIC
    from app.modules.integration.models import ExtSystem, ExtSystemStatus
    from app.modules.notifications.models import Notification
    from app.modules.notifications.service import page_subscription
    from app.modules.ontology.models import OntologyRoleBinding
    from app.modules.qa.models import (
        CardViz,
        PageSourceKind,
        QaPage,
        QaPageCard,
        QaPageSubscription,
        QaSession,
        QaTurn,
        SubChannel,
        SubWhen,
    )
    from app.modules.static.models import ObjectStatus, ObjectType
    from app.modules.versioning.repo import ReleaseRepo

    await set_ontology_scope(session, 1)
    assert require_ontology_id(session) == 1
    actor = await resolve_user(session, "U010001")
    assert actor.ontology_role in ("owner", "editor") and actor.func.get("api-keys") == "manage"
    owner = await session.scalar(select(SysUser).where(SysUser.code == actor.id))
    store_type = await session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    assert store_type is not None and store_type.status == ObjectStatus.published
    flag = next(
        prop
        for prop in store_type.properties
        if prop.backing_column == "is_test" and not prop.is_sensitive and prop.visibility != "hidden"
    )
    now = clock.now()
    page = await session.scalar(select(QaPage).where(QaPage.code == f"{PREFIX}-PAGE"))
    if page is None:
        page = QaPage(
            code=f"{PREFIX}-PAGE",
            name=f"{PREFIX} 订阅页面",
            owner_id=owner.id,
            source_kind=PageSourceKind.manual,
            refresh={"freq": "手动", "time": "--", "onFail": "保留上次结果"},
        )
        session.add(page)
        await session.flush()
        session.add(
            QaPageSubscription(
                page_id=page.id,
                enabled=True,
                channel=SubChannel.inbox,
                recipients=[owner.name],
                send_when=SubWhen.after_refresh,
            )
        )
        await session.flush()
    if page.refresh.get("freq") == "手动":
        page.refresh = {"time": "--", "onFail": "保留上次结果", **page.refresh}
    source = await session.scalar(select(OutboxEvent).where(OutboxEvent.event_id == f"{PREFIX}:subscription"))
    if source is None:
        source = await emit(
            session,
            "qa.page.subscription",
            {"ontologyId": 1, "pageId": page.code, "to": [owner.code], "channel": "inbox"},
            event_id=f"{PREFIX}:subscription",
        )
        await page_subscription(session, source)
        source.dispatched_at, source.attempts = now, 1
    functions = []
    for letter in ("A", "B"):
        code, name = f"{PREFIX}-FN-{letter}", f"uicheck_fix_{letter.lower()}"
        fn = await session.scalar(select(Function).where(Function.code == code))
        if fn is None:
            fn = Function(
                code=code,
                name=name,
                title=f"{PREFIX} 函数 {letter}",
                runtime="Python",
                status="published",
                owner_id=owner.id,
                timeout_s=1,
                description="测试用纯计算函数",
                code_text=f"def {name}(value: int) -> int:\n    return value + 1",
                sample_in={"value": 2},
            )
            session.add(fn)
            await session.flush()
            session.add(
                FnVersion(
                    function_id=fn.id,
                    ver="v1.0.0",
                    at=now,
                    author_id=owner.id,
                    note="隔离UI验证定义",
                    snapshot={},
                )
            )
        functions.append(code)
    key = await session.scalar(select(ApiKey).where(ApiKey.code == f"AK-{PREFIX}"))
    if key is None:
        token = "rg_test_" + secrets.token_urlsafe(32)
        session.add(
            ApiKey(
                code=f"AK-{PREFIX}",
                name="uicheck-fix-functions",
                env="测试",
                user_id=owner.id,
                principal_kind="user",
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                prefix=token[:12],
                last4=token[-4:],
                scopes=["meta:read", "functions:invoke"],
                ontology_ids=[1],
                func_snapshot={"functions": "view"},
                function_types=functions,
                ip_allow=[],
                note="隔离UI白名单验证；密钥明文不输出",
                creator=owner.name,
                created_by=owner.code,
            )
        )
    target_code, action_code, run_code = f"{PREFIX}-TARGET", f"{PREFIX}-ACTION", f"{PREFIX}-RUN"
    if await session.scalar(select(Store.id).where(Store.code == target_code)) is None:
        session.add(
            Store(
                code=target_code,
                store_no=target_code,
                name=f"{PREFIX} 测试目标",
                city="测试",
                region="UICHECK FIX",
                big_region="UICHECK FIX",
                format="测试",
                opened_at=date(2026, 1, 1),
                status=StoreStatus.open,
                is_test=False,
            )
        )
    system_code = f"{PREFIX}-SYS"
    if await session.scalar(select(ExtSystem.id).where(ExtSystem.code == system_code)) is None:
        session.add(
            ExtSystem(
                code=system_code,
                name=f"{PREFIX} 未连接通道",
                category="Webhook",
                vendor="测试",
                endpoint="https://127.0.0.1:9444",
                auth_type="无",
                auth_detail="",
                idempotent=True,
                idem_header="Idempotency-Key",
                timeout_ms=1000,
                max_retry=2,
                retry_text="",
                rate_limit="",
                owner=owner.name,
                status=ExtSystemStatus.ok,
                env="测试",
                usage="不配置适配器、不发送",
            )
        )
    role = await session.scalar(select(SysRole).where(SysRole.code == f"{PREFIX}-ROLE"))
    if role is None:
        role = SysRole(
            code=f"{PREFIX}-ROLE",
            name=f"{PREFIX} 测试动作操作",
            changed_at=now,
            changed_by=owner.name,
            func={"action-center": "manage", "external-systems": "manage"},
            objs=[
                {
                    "api": store_type.api_name,
                    "objType": store_type.display_name,
                    "view": True,
                    "actions": [action_code],
                    "export": False,
                }
            ],
        )
        session.add(role)
        await session.flush()
        session.add_all(
            [
                SysRoleMember(role_id=role.id, user_id=owner.id, seq=0),
                OntologyRoleBinding(ontology_id=1, role_id=role.id),
                SysRoleScope(
                    code=f"{PREFIX}-SCOPE",
                    role_id=role.id,
                    seq=0,
                    obj_type=store_type.display_name,
                    object_type_id=store_type.id,
                    object_type_code=store_type.code,
                    prop="大区",
                    op="=",
                    values=["UICHECK FIX"],
                ),
            ]
        )
    action = await session.scalar(select(ActionType).where(ActionType.code == action_code))
    if action is None:
        action = ActionType(
            code=action_code,
            api="UiCheckFixAction",
            name=f"{PREFIX} 受控动作",
            grp="测试",
            status=TypeStatus.published,
            version="v1",
            target=store_type.display_name,
            target_api=store_type.api_name,
            target_kind="store",
            approval="免审",
        )
        action.params = [ActionParam(seq=0, name="测试标记", api="enabled", type="布尔", required=True)]
        action.conds, action.rules = [], []
        action.edits = [
            OntologyEditRule(
                seq=0,
                op=EditOp.modify,
                obj="store",
                field=json.dumps({"values": {flag.api_name: {"param": "enabled"}}}),
            )
        ]
        action.effects = [
            SideEffect(
                seq=0,
                kind=EffectKind.webhook,
                target=f"{system_code}:hook",
                when_text="执行成功后",
                note=json.dumps({"payload": {"value": {"param": "enabled"}}}),
            )
        ]
        session.add(action)
        await session.flush()
        release = await ReleaseRepo(session).latest()
        session.add(
            ActionTypeVersion(
                action_type_id=action.id,
                ver="v1",
                release_ver=release.ver,
                at=now,
                by_name=owner.name,
                change_note="隔离UI定义",
                definition={**action_def(action), "policy": {"mode": "confirm", "overflow": "stage"}},
            )
        )
    if await session.scalar(select(ActionRun.id).where(ActionRun.code == run_code)) is None:
        session.add(
            ActionRun(
                code=run_code,
                action_type_code=action_code,
                action_name=action.name,
                type_version="v1",
                source_type=SourceType.manual,
                source_label="手动",
                obj_kind="store",
                obj_id=target_code,
                obj_label=f"{PREFIX} 测试目标",
                params={"enabled": False},
                initiator=owner.name,
                confirmer=owner.code,
                idem_key=run_code,
                status=ActionStatus.succeeded,
                submitted_at=now,
                finished_at=now,
                result_code="INTERNAL",
                review_snapshot={"typeVersion": "v1", "humanChecks": [], "declaredEdits": []},
            )
        )
    await session.flush()
    effect_ids = []
    for state, status in (("failed", 503), ("unknown", None)):
        identity = f"{PREFIX}:effect:{state}"
        event = await session.scalar(select(OutboxEvent).where(OutboxEvent.event_id == identity))
        if event is None:
            declaration = action_def(action)["writeback"]["effects"][0]
            event = await emit(
                session,
                TOPIC,
                {
                    "ontologyId": 1,
                    "runId": run_code,
                    "actor": owner.code,
                    "typeVersion": "v1",
                    "effect": declaration,
                    "data": {
                        "when": "ActionExecuted",
                        "payload": {"value": False},
                        "url": "https://127.0.0.1:9444/hook",
                    },
                    "state": state,
                    "attempts": [
                        {
                            "attempt": 1,
                            "at": now.isoformat(),
                            "httpStatus": status,
                            "error": "EXTERNAL_HTTP_503" if status else "EXTERNAL_OUTCOME_UNKNOWN",
                            "retryable": status == 503,
                            "signed": False,
                            "elapsedMs": 5,
                        }
                    ],
                },
                event_id=identity,
            )
            event.attempts = 1
            event.last_error = "EXTERNAL_HTTP_503" if status else "EXTERNAL_OUTCOME_UNKNOWN"
        effect_ids.append(str(event.id))
    await session.flush()
    if await session.scalar(select(QaPageCard.id).where(QaPageCard.page_id == page.id).limit(1)) is None:
        from app.modules.qa.page_execution import execute_cards, fingerprint, turn_plan

        source_turn = await session.scalar(
            select(QaTurn)
            .join(QaSession, QaSession.id == QaTurn.session_id)
            .where(QaTurn.code == "QA-UI-SOURCE-T001", QaSession.owner_id == owner.id)
        )
        release = await ReleaseRepo(session).latest()
        assert source_turn is not None
        plan = turn_plan(source_turn, release)
        actor = await resolve_user(session, owner.code)
        card = SimpleNamespace(viz=CardViz.table, sem_plan={"execution": plan}, pending_cr=None)
        caches, as_of = await execute_cards(session, actor, [card], release=release)
        permission = await fingerprint(actor)
        page.last_refresh, page.data_as_of = now, as_of
        session.add(
            QaPageCard(
                page_id=page.id,
                card_key="UICHECK-FIX-CARD",
                sort_order=0,
                title="当前授权门店目录",
                viz=CardViz.table,
                sem_plan=card.sem_plan,
                source_turn_id=source_turn.id,
                result_cache=caches[0],
                perm_fingerprint=permission,
            )
        )
        await session.flush()
    notification_ids = list(
        await session.scalars(select(Notification.id).where(Notification.source_outbox_id == source.id))
    )
    return {
        "page": page.code,
        "notificationIds": [str(value) for value in notification_ids],
        "key": f"AK-{PREFIX}",
        "functionIds": functions,
        "action": action_code,
        "run": run_code,
        "effectIds": effect_ids,
    }
