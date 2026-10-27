import json
from datetime import date

import pytest
from sqlalchemy import func, select

from app.common.auth import resolve_user
from app.core import clock
from app.modules.action.checker import DatabaseSubmissionChecker
from app.modules.action.condition_expr import Unavailable, evaluate
from app.modules.action.models import (
    ActionParam,
    ActionType,
    CriterionKind,
    EditOp,
    OntologyEditRule,
    SubmissionCriterion,
    TypeStatus,
)
from app.modules.admin.models import SysRole, SysRoleScope
from app.modules.biz.models import Store, StoreStatus
from app.modules.dynamic.models import ActionRun, Alert, AlertLevel, AlertStatus
from app.modules.static.cr_write import CrWriteService
from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectType, Property
from app.modules.static.schemas import CRCreateIn, ItemIn, ItemsPutIn, TargetIn
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.service import VersioningService
from tests.use_ops_support import grant_ops_roles as grant_fixture_roles

B = "/api/ontology/v1"


@pytest.fixture
async def checked(users):
    await grant_fixture_roles(users, {"U010001": "R-01"})
    action = ActionType(
        code="checker_actual",
        api="CheckerActual",
        name="真实校验动作",
        grp="测试",
        status=TypeStatus.published,
        version="v1",
        target="门店",
        target_api="Store",
        target_kind="store",
        approval="免审",
    )
    users.add(action)
    obj = ObjectType(
        code="store",
        api_name="Store",
        display_name="门店",
        obj_group="测试",
        conflict_strategy=ConflictStrategy.user_edit_wins,
        lifecycle=Lifecycle.active,
    )
    obj.properties = [
        Property(
            api_name="status",
            display_name="状态",
            data_type="enum",
            prop_group="基本",
            source_column="store.status",
            backing_column="status",
        )
    ]
    users.add(obj)
    await users.flush()
    users.add(
        ActionParam(
            action_type_id=action.id,
            seq=0,
            name="数量",
            api="qty",
            type="整数",
            required=True,
            rule_text="参数.数量 > 0",
        )
    )
    users.add(
        SubmissionCriterion(
            action_type_id=action.id,
            seq=0,
            name="数量上限",
            kind=CriterionKind.parameter,
            expr_text="1 ≤ 参数.数量 ≤ 8",
            fail_msg="数量须在1到8之间",
        )
    )
    users.add(
        SubmissionCriterion(
            action_type_id=action.id,
            seq=1,
            name="真实角色",
            kind=CriterionKind.current_user,
            expr_text="当前用户.角色 ∈ {系统管理员}",
            fail_msg="需要实际系统管理员角色",
        )
    )
    await users.flush()
    return users, action


async def test_action_dry_run_real_params_roles_failures_and_no_write(checked, client):
    session, action = checked
    response = await client.post(f"{B}/action-types/{action.code}:dry-run", json={"params": {"qty": 3}})
    assert response.status_code == 200, response.text
    assert response.json() == {"passed": True, "checked": 2, "failed": []}
    response = await client.post(f"{B}/action-types/{action.code}:dry-run", json={"params": {"qty": 30}})
    assert response.status_code == 200 and response.json()["passed"] is False
    assert response.json()["failed"] == [{"name": "数量上限", "fail": "数量须在1到8之间"}]
    response = await client.post(f"{B}/action-types/{action.code}:dry-run", json={})
    assert response.json()["passed"] is False and response.json()["checked"] == 2
    assert any(item["name"] == "数量" for item in response.json()["failed"])
    cond = await session.scalar(select(SubmissionCriterion).where(SubmissionCriterion.name == "数量上限"))
    cond.expr_text = "不存在 外部标签 where 状态 = 启用"
    await session.flush()
    response = await client.post(f"{B}/action-types/{action.code}:dry-run", json={"params": {"qty": 3}})
    assert response.json()["passed"] is False
    assert "未支持" in response.json()["failed"][0]["fail"]
    assert await session.scalar(select(func.count()).select_from(ActionRun)) == 0


async def test_action_dry_run_history_and_real_cr_delete(checked, client):
    session, action = checked
    actor = await resolve_user(session, "U010001")
    writer = CrWriteService(session)
    target = TargetIn(kind="action", id=action.code)
    cr = await writer.create(actor, CRCreateIn(title="真实校验草稿", target=target))
    await writer.put_items(
        cr, actor, ItemsPutIn(target=target, section="conds", rows=[ItemIn(key="数量上限", op="delete")])
    )
    response = await client.post(
        f"{B}/action-types/{action.code}:dry-run?view=draft&cr={cr}", json={"params": {"qty": 30}}
    )
    assert response.status_code == 200 and response.json() == {"passed": True, "checked": 1, "failed": []}
    response = await client.post(f"{B}/action-types/{action.code}:dry-run?view=draft&cr=missing", json={})
    assert response.status_code == 404
    # Persist an actual independent version, then change its current definition.
    ver = await VersioningService().create_version(session, [cr], published_by="U010001", note="校验历史")
    release = await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))
    # Use a real builder for a second release rather than mutating append-only history.
    from app.modules.action.sections import BUILDER
    from app.modules.versioning.sections import register_section

    register_section(BUILDER)
    ver = await VersioningService().create_version(session, [cr], published_by="U010001", note="历史动作定义")
    cond = await session.scalar(select(SubmissionCriterion).where(SubmissionCriterion.name == "数量上限"))
    cond.expr_text = "参数.qty < 0"
    await session.flush()
    response = await client.post(
        f"{B}/action-types/{action.code}:dry-run?ver={ver}", json={"params": {"qty": 3}}
    )
    assert response.status_code == 200 and response.json()["passed"] is True
    response = await client.post(
        f"{B}/action-types/{action.code}:dry-run?ver={release.ver}", json={"params": {"qty": 3}}
    )
    assert response.status_code == 503 and response.json()["code"] == "ACTION_DEFINITION_UNAVAILABLE"
    response = await client.post(f"{B}/action-types/{action.code}:dry-run?previewAs=U010001", json={})
    assert response.status_code == 403 and response.json()["code"] == "READONLY"


async def test_action_shared_checker_json_port_and_bad_inputs(checked, client):
    session, action = checked
    actor = await resolve_user(session, "U010001")
    checker = DatabaseSubmissionChecker(session, actor)
    assert await checker.check(action.code, {"params": '{"qty":3}'}) == []
    assert await checker.check(action.code, {"params": "参数摘要：数量3"})
    for params in ({"qty": True}, {"qty": 3, "extra": 1}, {"qty": "not-number"}):
        response = await client.post(f"{B}/action-types/{action.code}:dry-run", json={"params": params})
        assert response.status_code == 200 and response.json()["passed"] is False
    for params in ({"qty": 3, "x": "\x00"}, {"qty": 3, "x": "\ud800"}):
        response = await client.post(
            f"{B}/action-types/{action.code}:dry-run",
            content=json.dumps({"params": params}),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
    assert (await client.post(f"{B}/action-types/bad%00:dry-run", json={})).status_code == 422
    assert (
        await client.post(f"{B}/action-types/{action.code}:dry-run?view=draft&cr=x%00", json={})
    ).status_code == 422
    assert await session.scalar(select(func.count()).select_from(ActionRun)) == 0
    # The default dynamic port is the same real checker; failed criteria cannot create a run.
    action.edits = [
        OntologyEditRule(
            seq=0, op=EditOp.modify, obj="store", field='{"values":{"status":{"value":"营业中"}}}'
        )
    ]
    role = await session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    rows = [dict(row) for row in role.objs]
    entry = next((row for row in rows if row.get("api") == "Store"), None)
    if entry is None:
        entry = {"api": "Store", "objType": "门店", "view": True, "actions": []}
        rows.append(entry)
    entry["actions"] = [*entry.get("actions", []), action.code]
    role.objs = rows
    session.add(
        Store(
            code="S-test",
            store_no="S-test",
            name="测试对象",
            city="测试",
            region="华东一区",
            big_region="华东",
            format="测试",
            opened_at=date(2026, 1, 1),
            status=StoreStatus.open,
        )
    )
    await session.flush()
    response = await client.post(
        f"{B}/action-runs:stage",
        json={
            "typeId": action.code,
            "source": {"type": "手动"},
            "objKind": "store",
            "objId": "S-test",
            "objLabel": "测试对象",
            "params": '{"qty":30}',
            "idempotencyKey": "checker-invalid-params",
        },
    )
    assert response.status_code == 422 and response.json()["code"] == "SUBMISSION_CRITERIA_FAILED"
    assert await session.scalar(select(func.count()).select_from(ActionRun)) == 0
    param = await session.scalar(select(ActionParam).where(ActionParam.action_type_id == action.id))
    param.rule_text, param.rule_expr = "—", {"unknown": True}
    await session.flush()
    response = await client.post(f"{B}/action-types/{action.code}:dry-run", json={"params": {"qty": 3}})
    assert response.json()["passed"] is False
    assert any("AST" in item["fail"] for item in response.json()["failed"])


async def test_action_object_condition_uses_real_scope_and_sensitive_flags(checked):
    session, action = checked
    for code, region in (("S-east", "华东"), ("S-west", "华西")):
        session.add(
            Store(
                code=code,
                store_no=code,
                name=code,
                city="测试",
                region=region,
                big_region=region,
                format="社区店",
                opened_at=clock.today(),
                status=StoreStatus.open,
            )
        )
    role = await session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    session.add(
        SysRoleScope(
            code="S-checker", role_id=role.id, seq=1, obj_type="门店", prop="大区", op="=", values=["华东"]
        )
    )
    session.add(
        SubmissionCriterion(
            action_type_id=action.id,
            seq=2,
            name="对象状态",
            kind=CriterionKind.context_object,
            expr_text="门店.状态 = 营业中",
            fail_msg="门店不可执行",
        )
    )
    await session.flush()
    checker = DatabaseSubmissionChecker(session, await resolve_user(session, "U010001"))
    assert (
        await checker.check(action.code, {"params": {"qty": 3}, "objKind": "store", "objId": "S-east"}) == []
    )
    failed = await checker.check(action.code, {"params": {"qty": 3}, "objKind": "store", "objId": "S-west"})
    assert [row["name"] for row in failed] == ["对象状态"]
    assert "上下文" in failed[0]["fail"] and "华西" not in failed[0]["fail"]
    prop = await session.scalar(select(Property).where(Property.api_name == "status"))
    prop.is_sensitive = True
    await session.flush()
    assert await checker.check(action.code, {"params": {"qty": 3}, "objKind": "store", "objId": "S-east"})


async def test_action_alert_conditions_only_read_actual_current_assignee(checked):
    session, action = checked
    alert = Alert(
        code="AL-checker",
        rule_code="R-checker",
        level=AlertLevel.medium,
        obj_kind="store",
        obj_id="S-owned",
        obj_label="实际对象",
        value="1",
        threshold="0",
        triggered_at=clock.now(),
        data_cutoff=clock.now(),
        status=AlertStatus.processing,
        assignee="U010001",
    )
    session.add(alert)
    session.add(
        SubmissionCriterion(
            action_type_id=action.id,
            seq=2,
            name="本人告警",
            kind=CriterionKind.current_user,
            expr_text="告警.处理人 = 当前用户 且 告警.状态 = 处理中",
            fail_msg="告警不可处理",
        )
    )
    await session.flush()
    checker = DatabaseSubmissionChecker(session, await resolve_user(session, "U010001"))
    assert await checker.check(action.code, {"params": {"qty": 3}, "alertId": alert.code}) == []
    alert.assignee = "U010002"
    await session.flush()
    failed = await checker.check(action.code, {"params": {"qty": 3}, "alertId": alert.code})
    assert [row["name"] for row in failed] == ["本人告警"]
    assert "上下文" in failed[0]["fail"] and "U010002" not in failed[0]["fail"]


def test_condition_whitelist_budget_and_real_boolean_results():
    refs = {
        ("参数", "数量"): 4,
        ("参数", "原因"): "实际原因",
        ("当前用户", "角色"): ["R-01", "系统管理员"],
        ("当前用户", "编号"): "U010001",
    }
    assert evaluate("1 ≤ 参数.数量 ≤ 8 且 参数.原因 ≠ 空", refs)
    assert evaluate("1≤参数.数量≤8 且 参数.原因≠空", refs)
    assert evaluate("当前用户.角色 ∈ {系统管理员, 门店店长}", refs)
    assert not evaluate("当前用户.角色 = 会员运营", refs)
    for value in (
        "True or __import__('os')",
        "True # dropped\n and False",
        "参数.缺失 = 空",
        "2 ** 99999 > 0",
        "参数.数量.__class__ = int",
        "自然语言规则",
        "True or 对象.缺失 = 1",
        "True or 未知 = 未知",
        "参数.原因 < 今天",
    ):
        with pytest.raises(Unavailable):
            evaluate(value, refs)
