"""真实发布事务验证七段写回、改名、内部数据保留和失败回滚。"""

import pytest
from sqlalchemy import func, select

from app.modules.action import sections as action_sections
from app.modules.action.models import (
    ActionParam,
    ActionType,
    ActionTypeRule,
    ActionTypeVersion,
    SubmissionCriterion,
)
from app.modules.event import sections as event_sections
from app.modules.event.models import EventEnvelopeField, EventSubscription, EventType, EventTypeVersion
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from app.modules.static.models import ChangeRequest, CrEvent, CrItem, Knowledge, ObjectType
from app.modules.versioning.models import OntologyRelease
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1"
AUTHOR = {"X-Demo-As": "U010004"}
ADMIN = {"X-Demo-As": "U010001"}
A = {"kind": "action", "id": "wb_action", "name": "写回动作", "isNew": True}
E = {"kind": "event", "id": "wb_event", "name": "写回事件", "isNew": True}
PARAM = {"name": "参数", "api": "p", "type": "字符串", "required": True, "def": "无", "rule": "x > 0"}
COND = {"kind": "参数", "name": "条件", "expr": "p > 0", "fail": "须为正数"}
EDIT = {"op": "修改", "obj": "门店", "field": "名称"}
EFFECT = {"kind": "通知", "target": "运维", "when": "写回成功后", "note": "说明"}
CONFIG = {"sysId": "EXT-ERP", "endpoint": "/write", "idemKey": "orderNo"}
FIELD = {"name": "业务值", "api": "value", "type": "字符串", "required": True, "desc": "说明", "sample": "1"}
SUB = {
    "kind": "通知",
    "name": "订阅方",
    "filter": "全部",
    "then": "通知",
    "status": "启用",
    "weekly": 999,
    "ruleId": None,
}


@pytest.fixture
async def wb(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        action_sections.register()
        event_sections.register()
        await run_seeds(session, ["admin", "integration", "action", "event", "versioning", "static"])
        await grant_fixture_roles(session)
        await session.commit()
        yield session


def row(key, data=None, op="add"):
    return {"key": key, "op": op, "after": data}


@pytest.mark.parametrize("target_code", ["store", "object_" + "x" * 57])
async def test_new_type_definition_fields_publish_and_rollback(client, wb, target_code):
    obj = await wb.scalar(select(ObjectType).where(ObjectType.code == "store"))
    if target_code != "store":
        from app.modules.static.writeback import DIRTY

        values = {
            column.key: getattr(obj, column.key)
            for column in ObjectType.__table__.columns
            if column.key not in {"id", "code", "api_name", "is_seed", "seed_tag"}
        }
        wb.add(ObjectType(**values, code=target_code, api_name="LongTargetObject", is_seed=False))
        wb.info[DIRTY] = {"objectTypes"}
        await wb.flush()
    action = {
        "name": "真实新动作",
        "api": A["id"],
        "group": "业务",
        "targetKind": target_code,
        "owner": "刘婷",
        "desc": "动作说明",
        "runners": "建模人员",
        "approval": "负责人审批",
    }
    event = {
        "name": "真实新事件",
        "api": E["id"],
        "source": "外部系统",
        "sourceRef": "业务上报",
        "objKind": target_code,
        "owner": "刘婷",
        "desc": "事件说明",
        "retention": "90 天",
        "dedupKey": "eventId",
        "dedupWindow": "10 分钟",
        "delivery": "至少一次",
        "ordering": "同对象有序",
    }
    await approved(
        client,
        [
            (A, "def", [row(k, v) for k, v in action.items()]),
            (E, "def", [row(k, v) for k, v in event.items()]),
            (A, "params", [row("p", PARAM)]),
            (E, "payload", [row("value", FIELD)]),
        ],
    )
    ver = await publish(client)
    snap = await snapshot(wb, ver)
    actual_action, actual_event = snap["actionTypes"][A["id"]], snap["eventTypes"][E["id"]]
    assert actual_action["name"] == action["name"] and actual_action["group"] == "业务"
    assert actual_action["targetKind"] == target_code and actual_action["target"] == "门店"
    assert actual_event["sourceRef"] == "业务上报" and actual_event["retention"] == "90 天"
    assert actual_event["objKind"] == target_code and actual_event["object"] == "门店"
    assert actual_action["version"] == actual_event["version"] == "v1"
    response = await client.post(f"{P}/releases/{ver}:rollback", json={}, headers=ADMIN)
    assert response.status_code == 201, response.text
    restored = await snapshot(wb, response.json()["ver"])
    assert A["id"] not in restored["actionTypes"] and E["id"] not in restored["eventTypes"]


@pytest.mark.parametrize(
    "target,field,value", [(A, "targetKind", "missing"), (E, "retention", "0 天"), (E, "weekly", 99)]
)
async def test_invalid_definition_publish_is_atomic(client, wb, target, field, value):
    cr = await approved(client, [(target, "def", [row(field, value)])])
    before = await wb.scalar(select(func.count()).select_from(OntologyRelease))
    response = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert response.status_code == 409 and response.json()["code"] == "WRITEBACK_FAILED"
    wb.expire_all()
    assert await wb.scalar(select(func.count()).select_from(OntologyRelease)) == before
    assert (await wb.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))).status.value == "已通过"
    model = ActionType if target["kind"] == "action" else EventType
    assert await wb.scalar(select(model).where(model.code == target["id"])) is None


async def approved(client, changes):
    result = await client.post(P + "/change-requests", json={"title": "动作事件写回"}, headers=AUTHOR)
    assert result.status_code == 201, result.text
    cr = result.json()["id"]
    for target, section, rows in changes:
        result = await client.put(
            f"{P}/change-requests/{cr}/items",
            json={"target": target, "section": section, "rows": rows},
            headers=AUTHOR,
        )
        assert result.status_code == 200, result.text
    for verb, user in [("submit", AUTHOR), ("approve", ADMIN)]:
        result = await client.post(f"{P}/change-requests/{cr}:{verb}", headers=user)
        assert result.status_code == 200, result.text
    return cr


async def publish(client):
    result = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert result.status_code == 201, result.text
    return result.json()["ver"]


async def snapshot(session, ver):
    session.expire_all()
    release = await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))
    return release.snapshot


def seven(op="add"):
    return [
        (A, "params", [row("p", PARAM, op)]),
        (A, "conds", [row("条件", COND, op)]),
        (A, "edits", [row("修改:门店", EDIT, op)]),
        (A, "effects", [row("运维", EFFECT, op)]),
        (A, "config", [row("cfg", CONFIG, op)]),
        (E, "payload", [row("value", FIELD, op)]),
        (E, "subs", [row("订阅方", SUB, op)]),
    ]


async def test_seven_segments_add_rename_update_delete_and_versions(client, wb):
    cr = await approved(client, seven())
    first = await publish(client)
    snap1 = await snapshot(wb, first)
    a, e = snap1["actionTypes"][A["id"]], snap1["eventTypes"][E["id"]]
    assert (a["version"], e["version"], e["retention"], e["source"]) == ("v1", "v1", "30 天", "外部系统")
    assert a["params"] == [PARAM] and a["conds"] == [COND]
    assert a["writeback"] == {"edits": [EDIT], "effects": [EFFECT]}
    assert {k: a[k] for k in CONFIG} == CONFIG
    assert e["payload"] == [FIELD] and e["subscribers"] == [{k: v for k, v in SUB.items() if k != "weekly"}]
    parent = await wb.scalar(select(ActionType).where(ActionType.code == A["id"]))
    action_id = parent.id
    event = await wb.scalar(select(EventType).where(EventType.code == E["id"]))
    param, cond, sub = parent.params[0], parent.conds[0], event.subscribers[0]
    param.rule_expr, cond.expr_ast, sub.filter_expr = {"old": "param"}, {"old": "cond"}, {"old": "sub"}
    sub.weekly = 7
    wb.add(ActionTypeRule(action_type_id=parent.id, seq=1, rule_code="R-KEEP", generates=True))
    await wb.commit()
    ids = (param.id, cond.id, sub.id)
    # 更新身份字段但不修改表达式；行 id、AST、统计及规则关系均保留。
    second_rows = [
        (A, "params", [row("p", {**PARAM, "api": "renamed"}, "update")]),
        (A, "conds", [row("条件", {**COND, "name": "新条件"}, "update")]),
        (A, "edits", [row("修改:门店", {**EDIT, "op": "新建", "obj": "商品"}, "update")]),
        (A, "effects", [row("运维", {**EFFECT, "target": "业务"}, "update")]),
        (A, "config", [row("cfg", {"sysId": None, "endpoint": "—", "idemKey": "internal"}, "update")]),
        (E, "payload", [row("value", {**FIELD, "api": "value2"}, "update")]),
        (E, "subs", [row("订阅方", {**SUB, "name": "新订阅方"}, "update")]),
    ]
    second_rows = [({**t, "isNew": False}, s, r) for t, s, r in second_rows]
    await approved(client, second_rows)
    second = await publish(client)
    snap2 = await snapshot(wb, second)
    a2, e2 = snap2["actionTypes"][A["id"]], snap2["eventTypes"][E["id"]]
    assert a2["version"] == e2["version"] == "v2"
    assert a2["params"][0]["api"] == "renamed" and a2["conds"][0]["name"] == "新条件"
    assert a2["writeback"]["edits"] == [{**EDIT, "op": "新建", "obj": "商品"}]
    assert a2["writeback"]["effects"][0]["target"] == "业务" and a2["sysId"] is None
    assert e2["payload"][0]["api"] == "value2" and e2["subscribers"][0]["name"] == "新订阅方"
    p = await wb.get(ActionParam, ids[0])
    c = await wb.get(SubmissionCriterion, ids[1])
    s = await wb.get(EventSubscription, ids[2])
    assert (p.rule_expr, c.expr_ast, s.filter_expr, s.weekly) == (
        {"old": "param"},
        {"old": "cond"},
        {"old": "sub"},
        7,
    )
    assert (await wb.scalar(select(ActionTypeRule).where(ActionTypeRule.rule_code == "R-KEEP"))).generates
    # 只改文本时清空相应 AST，避免旧表达式与新文本不一致。
    await approved(
        client,
        [
            ({**A, "isNew": False}, "params", [row("renamed", {"rule": "new"}, "update")]),
            ({**A, "isNew": False}, "conds", [row("新条件", {"expr": "new"}, "update")]),
            ({**E, "isNew": False}, "subs", [row("新订阅方", {"filter": "new"}, "update")]),
        ],
    )
    await publish(client)
    wb.expire_all()
    assert (await wb.get(ActionParam, ids[0])).rule_expr is None
    assert (await wb.get(SubmissionCriterion, ids[1])).expr_ast is None
    assert (await wb.get(EventSubscription, ids[2])).filter_expr is None
    delete_keys = ["renamed", "新条件", "新建:商品", "业务", "cfg", "value2", "新订阅方"]
    await approved(
        client,
        [
            ({**t, "isNew": False}, section, [row(k, op="delete")])
            for (t, section, _), k in zip(seven(), delete_keys, strict=True)
        ],
    )
    fourth = await publish(client)
    final = await snapshot(wb, fourth)
    af, ef = final["actionTypes"][A["id"]], final["eventTypes"][E["id"]]
    assert af["params"] == af["conds"] == af["writeback"]["edits"] == af["writeback"]["effects"] == []
    assert ef["payload"] == ef["subscribers"] == [] and af["sysId"] is None and af["idemKey"] == ""
    assert af["version"] == ef["version"] == "v4"
    histories = list(
        await wb.scalars(select(ActionTypeVersion).where(ActionTypeVersion.action_type_id == action_id))
    )
    assert [h.ver for h in histories] == ["v1", "v2", "v3", "v4"]
    assert histories[0].definition == a and histories[1].definition == a2
    assert histories[0].by_name == "王敏" and histories[0].release_ver == first
    c = await wb.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))
    event = await wb.scalar(select(CrEvent).where(CrEvent.cr_id == c.id, CrEvent.action == "publish"))
    assert "未写回定义表：action" not in event.desc and "event/" not in event.desc
    assert (await snapshot(wb, first))["actionTypes"][A["id"]] == a


@pytest.mark.parametrize(
    "target,section,key,data",
    [
        (A, "params", "p", {**PARAM, "api": "x" * 65}),
        (A, "params", "p", {**PARAM, "required": "true"}),
        (A, "params", "wrong", PARAM),
        (A, "params", "p", {**PARAM, "rule_expr": {}}),
        (A, "conds", "条件", {**COND, "kind": []}),
        (A, "edits", "修改:门店", {**EDIT, "op": "删除"}),
        (A, "effects", "运维", {**EFFECT, "kind": "Unknown"}),
        (A, "config", "cfg", {**CONFIG, "sysId": "DOES-NOT-EXIST"}),
        (A, "config", "cfg", {**CONFIG, "endpoint": "x" * 256}),
        (E, "payload", "value", {**FIELD, "type": {"bad": True}}),
        (E, "subs", "订阅方", {**SUB, "status": "Unknown"}),
        ({**A, "isNew": False}, "params", "p", PARAM),
    ],
)
async def test_invalid_publish_is_409_and_whole_transaction_unchanged(client, wb, target, section, key, data):
    changes = [(target, section, [row(key, data)])]
    if target["kind"] == "event":
        changes.insert(0, (A, "params", [row("p", PARAM)]))
    cr = await approved(client, changes)
    before = (await client.get(f"{P}/change-requests/{cr}", headers=ADMIN)).json()
    releases = int(await wb.scalar(select(func.count()).select_from(OntologyRelease)))
    versions = int(await wb.scalar(select(func.count()).select_from(ActionTypeVersion)))
    event_versions = int(await wb.scalar(select(func.count()).select_from(EventTypeVersion)))
    definition = await wb.scalar(select(Knowledge.definition).where(Knowledge.code == "K-002"))
    items_before = (await client.get(f"{P}/change-requests/{cr}/items", headers=ADMIN)).json()
    result = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert result.status_code == 409 and result.json()["code"] == "WRITEBACK_FAILED", result.text
    wb.expire_all()
    assert int(await wb.scalar(select(func.count()).select_from(OntologyRelease))) == releases
    assert int(await wb.scalar(select(func.count()).select_from(ActionTypeVersion))) == versions
    assert int(await wb.scalar(select(func.count()).select_from(EventTypeVersion))) == event_versions
    assert await wb.scalar(select(ActionType.id).where(ActionType.code == A["id"])) is None
    assert await wb.scalar(select(EventType.id).where(EventType.code == E["id"])) is None
    after = (await client.get(f"{P}/change-requests/{cr}", headers=ADMIN)).json()
    assert (after["status"], after["lockVersion"]) == (before["status"], before["lockVersion"])
    assert await wb.scalar(select(Knowledge.definition).where(Knowledge.code == "K-002")) == definition
    assert (await client.get(f"{P}/change-requests/{cr}/items", headers=ADMIN)).json() == items_before


async def test_duplicate_seed_key_and_envelope_collision_rejected(client, wb):
    envelope = await wb.scalar(select(EventEnvelopeField.api).limit(1))
    # 不改变种子重复 api 的兼容；发布遇歧义明确拒绝，不能只改其中一条。
    assert (
        int(
            await wb.scalar(
                select(func.count())
                .select_from(ActionParam)
                .join(ActionType)
                .where(ActionType.code == "claim_alert", ActionParam.api == "alert")
            )
        )
        == 2
    )
    for target, section, key, data in [
        ({"kind": "action", "id": "claim_alert"}, "params", "alert", {"name": "不应修改"}),
        (E, "payload", envelope, {**FIELD, "api": envelope}),
    ]:
        cr = await approved(
            client, [(target, section, [row(key, data, "update" if section == "params" else "add")])]
        )
        result = await client.post(P + "/releases", json={}, headers=ADMIN)
        assert result.status_code == 409 and result.json()["code"] == "WRITEBACK_FAILED", result.text
        # 放弃当前单，下一次失败验证不受它影响。
        result = await client.post(f"{P}/change-requests/{cr}:unapprove", headers=ADMIN)
        assert result.status_code == 200, result.text
        result = await client.delete(f"{P}/change-requests/{cr}", headers=AUTHOR)
        assert result.status_code == 204, result.text


async def test_rollback_then_republish_does_not_reuse_type_version(client, wb):
    await approved(client, seven())
    await publish(client)
    await approved(client, [({**A, "isNew": False}, "params", [row("p", {"name": "第二版"}, "update")])])
    second = await publish(client)
    result = await client.post(f"{P}/releases/{second}:rollback", json={}, headers=ADMIN)
    assert result.status_code == 201, result.text
    wb.expire_all()
    at = await wb.scalar(select(ActionType).where(ActionType.code == A["id"]))
    action_id = at.id
    assert at.version == "v1" and at.params[0].name == PARAM["name"]
    await approved(client, [({**A, "isNew": False}, "params", [row("p", {"name": "重新发布"}, "update")])])
    third = await publish(client)
    snap = await snapshot(wb, third)
    assert snap["actionTypes"][A["id"]]["version"] == "v3"
    assert list(
        await wb.scalars(
            select(ActionTypeVersion.ver)
            .where(ActionTypeVersion.action_type_id == action_id)
            .order_by(ActionTypeVersion.id)
        )
    ) == ["v1", "v2", "v3"]


async def test_two_crs_same_target_finalize_once(wb):
    """生产合并校验通常阻止重叠定义；钩子本身仍保证跨单只追加一次。"""
    from app.core import clock
    from app.modules.static.models import CRLevel, CRStatus
    from app.modules.static.writeback import apply_crs

    crs = []
    at = await wb.scalar(select(ActionType).where(ActionType.code == "claim_alert"))
    old_versions = list(
        await wb.scalars(select(ActionTypeVersion.ver).where(ActionTypeVersion.action_type_id == at.id))
    )
    for index, (section, key, data) in enumerate(
        [
            ("effects", "新通知", {**EFFECT, "target": "新通知"}),
            ("conds", "新条件", {**COND, "name": "新条件"}),
        ]
    ):
        cr = ChangeRequest(
            code=f"CR-WB-{index}",
            title="拆分段",
            owner="刘婷",
            reviewer="王敏",
            status=CRStatus.approved,
            level=CRLevel.minor,
            status_at=clock.now(),
        )
        wb.add(cr)
        await wb.flush()
        wb.add(
            CrItem(
                cr_id=cr.id,
                target_kind="action",
                target_id=at.code,
                section=section,
                item_key=key,
                op="add",
                before=None,
                after=data,
                seq=1,
            )
        )
        crs.append(cr)
    await apply_crs(wb, crs, "v2099.01.1", clock.now(), "U010001")
    result = await action_sections.BUILDER.build_section(wb, None, [c.code for c in crs])
    await wb.flush()
    histories = list(
        await wb.scalars(select(ActionTypeVersion).where(ActionTypeVersion.action_type_id == at.id))
    )
    assert len(histories) == len(old_versions) + 1
    assert histories[-1].definition == result[at.code]
    assert any(x["target"] == "新通知" for x in histories[-1].definition["writeback"]["effects"])
    assert any(x["name"] == "新条件" for x in histories[-1].definition["conds"])
    history_id, saved_definition = histories[-1].id, histories[-1].definition
    await run_seeds(wb, ["action", "event"])
    wb.expire_all()
    assert (await wb.get(ActionTypeVersion, history_id)).definition == saved_definition


async def test_internal_hook_nul_defense_and_failed_pending_cleared(wb):
    from types import SimpleNamespace

    from app.core import clock
    from app.core.errors import AppError
    from app.modules.action.writeback import PENDING, params
    from app.modules.static.writeback import Ctx

    for bad in ["x\0y", "\ud800"]:
        ctx = Ctx(
            wb,
            SimpleNamespace(code="CR-INTERNAL", title="内部调用"),
            "v2099.01.1",
            clock.now(),
            "U010001",
            True,
            "新动作",
        )
        with pytest.raises(AppError) as error:
            async with wb.begin_nested():
                await params(
                    ctx, "internal_bad", [CrItem(item_key="p", op="add", after={**PARAM, "name": bad})]
                )
        assert error.value.status_code == 409 and error.value.code == "WRITEBACK_FAILED"
        assert PENDING not in wb.info
        assert await wb.scalar(select(ActionType.id).where(ActionType.code == "internal_bad")) is None


async def test_rename_collision_does_not_modify_original_rows(client, wb):
    await approved(client, [(A, "params", [row("p", PARAM), row("q", {**PARAM, "api": "q"})])])
    await publish(client)
    cr = await approved(client, [({**A, "isNew": False}, "params", [row("p", {"api": "q"}, "update")])])
    result = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert result.status_code == 409 and result.json()["code"] == "WRITEBACK_FAILED", result.text
    wb.expire_all()
    at = await wb.scalar(select(ActionType).where(ActionType.code == A["id"]))
    assert at.version == "v1" and [p.api for p in at.params] == ["p", "q"]
    assert (await client.get(f"{P}/change-requests/{cr}", headers=ADMIN)).json()["status"] == "已通过"


@pytest.mark.parametrize("field,value", [("env", "测试"), ("status", ExtSystemStatus.disabled)])
async def test_config_rejects_ineligible_existing_system(client, wb, field, value):
    system = await wb.scalar(select(ExtSystem).where(ExtSystem.code == CONFIG["sysId"]))
    setattr(system, field, value)
    await wb.commit()
    await approved(client, [(A, "config", [row("cfg", CONFIG)])])
    result = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert result.status_code == 409 and result.json()["code"] == "WRITEBACK_FAILED", result.text


async def test_republish_retired_types_restores_them_without_reusing_history(client, wb):
    await approved(client, seven())
    first = await publish(client)
    result = await client.post(f"{P}/releases/{first}:rollback", json={}, headers=ADMIN)
    assert result.status_code == 201, result.text
    for kind, tid in [("action", A["id"]), ("event", E["id"])]:
        assert (await client.get(f"{P}/{kind}-types/{tid}", headers=ADMIN)).status_code == 404
    await approved(
        client,
        [
            ({**A, "isNew": False}, "params", [row("p", {"name": "重新启用"}, "update")]),
            ({**E, "isNew": False}, "payload", [row("value", {"name": "重新启用"}, "update")]),
        ],
    )
    second = await publish(client)
    snap = await snapshot(wb, second)
    assert snap["actionTypes"][A["id"]]["version"] == snap["eventTypes"][E["id"]]["version"] == "v2"
    for kind, tid in [("action", A["id"]), ("event", E["id"])]:
        assert (await client.get(f"{P}/{kind}-types/{tid}", headers=ADMIN)).status_code == 200
