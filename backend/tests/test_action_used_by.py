"""动作引用：真实规则、当前告警及历史成员边界。"""

from sqlalchemy import func, select

from app.core import clock
from app.modules.action.models import ActionType, ActionTypeRule
from app.modules.admin.models import SysUser
from app.modules.dynamic.models import Alert, AlertStatus
from app.modules.dynamic.rules_models import Rule
from app.modules.versioning.models import OntologyRelease
from tests.dynamic_support import API, dyn  # noqa: F401


async def test_current_used_by(dyn, client):  # noqa: F811
    type_id = await dyn.scalar(select(ActionType.code).join(ActionTypeRule))
    action = (await client.get(f"{API}/action-types/{type_id}")).json()
    type_id = action["id"]
    rows = (await client.get(f"{API}/action-types/{type_id}/used-by")).json()["items"]
    assert [r["ruleId"] for r in rows] == action["usedBy"]
    for row in rows:
        code = row["ruleId"]
        assert row["alerts"] == await dyn.scalar(
            select(func.count()).select_from(Alert).where(Alert.rule_code == code)
        )
        assert row["openAlerts"] == await dyn.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.rule_code == code, Alert.status == AlertStatus.open)
        )
        rule = await dyn.scalar(select(Rule).where(Rule.code == code))
        assert row["ruleName"] == (rule.name if rule else f"{code}（定义不可用）")
    link = await dyn.scalar(select(ActionTypeRule).join(ActionType).where(ActionType.code == type_id))
    link.mode_text = "受控使用方式"
    await dyn.flush()
    rows = (await client.get(f"{API}/action-types/{type_id}/used-by")).json()["items"]
    assert next(r for r in rows if r["ruleId"] == link.rule_code)["mode"] == "受控使用方式"


async def test_historical_members_and_live_counts(dyn, client):  # noqa: F811
    release = OntologyRelease(
        ver="v2020.01.1",
        snapshot={},
        snapshot_sha256="0" * 64,
        published_by_id=await dyn.scalar(select(SysUser.id).limit(1)),
        published_at=clock.now(),
    )
    action = (await client.get(f"{API}/action-types")).json()["items"][0]
    code = await dyn.scalar(select(Alert.rule_code).limit(1))
    definition = {**action, "usedBy": ["MISSING", code]}
    release.snapshot = {
        "actionTypes": {action["id"]: definition},
        "rules": {
            code: {
                "name": "历史规则名",
                "trigger": {"kind": "定时"},
                "autoStage": {"enabled": True, "actionTypeId": action["id"], "confirmer": "历史确认人"},
            }
        },
    }
    dyn.add(release)
    alert = await dyn.scalar(select(Alert).where(Alert.rule_code == code))
    alert.status = AlertStatus.processing
    await dyn.flush()
    url = f"{API}/action-types/{action['id']}/used-by?ver={release.ver}"
    result = await client.get(url)
    assert result.status_code == 200, result.text
    missing, row = result.json()["items"]
    assert missing["ruleId"] == "MISSING" and missing["mode"] == "规则定义不可用"
    assert row["ruleName"] == "历史规则名" and row["trigger"] == "定时"
    assert "历史确认人" in row["mode"]
    assert row["openAlerts"] == await dyn.scalar(
        select(func.count())
        .select_from(Alert)
        .where(Alert.rule_code == code, Alert.status == AlertStatus.open)
    )
    assert row["alerts"] > row["openAlerts"]


async def test_used_by_boundaries(dyn, client):  # noqa: F811
    assert (await client.get(f"{API}/action-types/UNKNOWN/used-by")).status_code == 404
    assert (await client.get(f"{API}/action-types/bad%00id/used-by")).status_code == 422
    assert (
        await client.get(f"{API}/action-types/UNKNOWN/used-by", headers={"X-Demo-As": "unknown"})
    ).status_code == 401
