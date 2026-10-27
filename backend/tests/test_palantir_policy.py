"""动作类型执行策略：定义写回 / 发布校验 / 暂存分流（真实 PostgreSQL，无外部调用）。"""

import pytest
from sqlalchemy import func, select

from app.core import clock
from app.modules.action import policy as action_policy
from app.modules.action.models import ActionType, ActionTypeVersion
from app.modules.dynamic.models import ActionRun, ActionRunLog
from app.modules.versioning.models import OntologyRelease
from tests.test_action_event_writeback import ADMIN, A, P, approved, publish, row, snapshot, wb  # noqa: F401
from tests.test_use_ops_runtime import API, headers, payload, runtime  # noqa: F401
from tests.use_ops_support import bind_fixture_members

# 运行：pytest -p tests.use_ops_support tests/test_palantir_policy.py

AUTO = {"mode": "auto", "callers": ["人"], "limits": {"dailyCount": 2}}
SAVED = {**AUTO, "overflow": "stage"}  # 写回时规范化，补上默认 overflow


# ---------- 策略格式 ----------
@pytest.mark.parametrize(
    "value",
    [
        {"mode": "auto"},  # 自动必须有上限
        {"mode": "confirm", "limits": {"dailyCount": 1}},  # 只有自动能设上限
        {"mode": "auto", "limits": {"dailyCount": 0}},
        {"mode": "confirm", "callers": ["机器人"]},
        {"mode": "confirm", "callers": ["人", "人"]},
        {"mode": "confirm", "callers": []},
        {"mode": "confirm", "overflow": "reject"},
        {"mode": "confirm", "extra": 1},
        {"mode": "auto", "limits": {"dailyCount": 1, "amount": {"param": "qty", "max": -1}}},
        "auto",
    ],
)
def test_invalid_policy_rejected(value):
    with pytest.raises(ValueError):
        action_policy.parse(value)


def test_policy_roundtrip_and_fail_safe_default():
    full = {
        "mode": "auto",
        "callers": ["人", "AI"],
        "overflow": "stage",
        "limits": {
            "dailyCount": 5,
            "perCallerDailyCount": 2,
            "amount": {"param": "qty", "max": 500.0},
            "objects": ["S1"],
        },
    }
    assert action_policy.parse(full).dump() == full
    assert action_policy.stored(None) == action_policy.DEFAULT
    assert action_policy.stored({"mode": "auto"}).mode == "confirm"  # 历史脏数据不放宽
    assert action_policy.DEFAULT.callers is None  # 默认不限制来源 = 现有行为
    assert [action_policy.caller_of(s) for s in ("手动", "推演", "问数", "规则", "告警")] == [
        "人", "人", "AI", "规则", "规则"
    ]


# ---------- 变更单写回 + 发布校验 ----------
@pytest.fixture
async def pwb(wb):  # noqa: F811
    await bind_fixture_members(wb)  # wb 补授角色后再同步工作空间成员
    yield wb


def definition(approval):
    return {
        "name": "策略动作",
        "api": A["id"],
        "group": "业务",
        "targetKind": "store",
        "owner": "刘婷",
        "desc": "说明",
        "runners": "建模人员",
        "approval": approval,
    }


async def test_policy_publish_versions_snapshot_and_delete(client, pwb):
    await approved(
        client,
        [
            (A, "def", [row(k, v) for k, v in definition("免审").items()]),
            (A, "policy", [row("policy", AUTO)]),
        ],
    )
    snap = await snapshot(pwb, await publish(client))
    assert snap["actionTypes"][A["id"]]["policy"] == SAVED
    at = await pwb.scalar(select(ActionType).where(ActionType.code == A["id"]))
    stored = await pwb.scalar(
        select(ActionTypeVersion.definition).where(
            ActionTypeVersion.action_type_id == at.id, ActionTypeVersion.ver == at.version
        )
    )
    assert stored["policy"] == SAVED
    assert (await action_policy.load(pwb, A["id"], at.version)).mode == "auto"
    # 不改策略的后续发布沿用；删除回到默认，快照里不再出现 policy 键
    target = {**A, "isNew": False}
    await approved(client, [(target, "def", [row("desc", "新说明", op="update")])])
    snap = await snapshot(pwb, await publish(client))
    assert snap["actionTypes"][A["id"]]["policy"] == SAVED
    await approved(client, [(target, "policy", [row("policy", None, op="delete")])])
    snap = await snapshot(pwb, await publish(client))
    assert "policy" not in snap["actionTypes"][A["id"]]
    pwb.expire_all()
    at = await pwb.scalar(select(ActionType).where(ActionType.code == A["id"]))
    assert await action_policy.load(pwb, A["id"], at.version) == action_policy.DEFAULT
    assert all("policy" not in d for c, d in snap["actionTypes"].items() if c != A["id"])  # 存量形状不变


@pytest.mark.parametrize(
    "approval,policy_rows",
    [
        ("负责人审批", [row("policy", AUTO)]),  # 自动执行必须免审
        ("免审", [row("policy", {"mode": "auto"})]),
        ("免审", [row("other", AUTO)]),
    ],
)
async def test_invalid_policy_publish_is_409_and_atomic(client, pwb, approval, policy_rows):
    await approved(
        client, [(A, "def", [row(k, v) for k, v in definition(approval).items()]), (A, "policy", policy_rows)]
    )
    before = await pwb.scalar(select(func.count()).select_from(OntologyRelease))
    response = await client.post(P + "/releases", json={}, headers=ADMIN)
    assert response.status_code == 409 and response.json()["code"] == "WRITEBACK_FAILED", response.text
    pwb.expire_all()
    assert await pwb.scalar(select(func.count()).select_from(OntologyRelease)) == before
    assert await pwb.scalar(select(ActionType).where(ActionType.code == A["id"])) is None


# ---------- 暂存分流 ----------
async def set_policy(session, action, value):
    """版本表只追加：每次换一个新版本号写入策略，并把动作类型指到该版本。"""
    action.version = f"v{int(action.version[1:]) + 1}"
    session.add(
        ActionTypeVersion(
            action_type_id=action.id,
            ver=action.version,
            release_ver="t",
            at=clock.now(),
            by_name="test",
            definition={"policy": value},
        )
    )
    await session.flush()


async def stage(client, **patch):
    return await client.post(f"{API}/action-runs:stage", json=payload(**patch), headers=headers())


async def auto_logs(session):
    query = select(func.count()).select_from(ActionRunLog).where(ActionRunLog.text == action_policy.AUTO_TEXT)
    return int(await session.scalar(query) or 0)


def logged(result, text):
    return any(text in log["text"] for log in result.json()["log"])


async def test_default_policy_keeps_current_staging(runtime, client):  # noqa: F811
    session, _, _ = runtime
    result = await stage(client)
    assert result.status_code == 201, result.text
    assert result.json()["status"] == "待确认"
    assert await auto_logs(session) == 0


async def test_auto_within_daily_limit_then_overflow_to_stage(runtime, client):  # noqa: F811
    session, action, _ = runtime
    await set_policy(session, action, AUTO)
    for _ in (1, 2):
        result = await stage(client)
        assert result.status_code == 201, result.text
        body = result.json()
        assert body["status"] == "成功", body
        auto = [log for log in body["log"] if log["text"] == action_policy.AUTO_TEXT]
        assert len(auto) == 1, body["log"]
    result = await stage(client)
    assert result.status_code == 201 and result.json()["status"] == "待确认"
    assert logged(result, "今日自动执行已达上限 2")
    assert await auto_logs(session) == 2
    again = await client.post(f"{API}/action-runs/{result.json()['id']}:confirm", headers=headers())
    assert again.status_code == 200 and again.json()["status"] == "成功"  # 超限转人工后照常确认


async def test_auto_object_amount_and_per_caller_limits_overflow(runtime, client):  # noqa: F811
    session, action, _ = runtime
    await set_policy(session, action, {"mode": "auto", "limits": {"dailyCount": 5, "objects": ["OPS-S00"]}})
    result = await stage(client, objId="OPS-S01")
    assert result.json()["status"] == "待确认" and logged(result, "OPS-S01"), result.text
    limits = {"dailyCount": 5, "amount": {"param": "enabled", "max": 1}}
    await set_policy(session, action, {"mode": "auto", "limits": limits})
    result = await stage(client)
    assert result.json()["status"] == "待确认" and logged(result, "enabled"), result.text
    await set_policy(session, action, {"mode": "auto", "limits": {"dailyCount": 5, "perCallerDailyCount": 1}})
    assert (await stage(client)).json()["status"] == "成功"
    result = await stage(client)
    assert result.json()["status"] == "待确认" and logged(result, "调用方"), result.text
    assert await auto_logs(session) == 1


@pytest.mark.parametrize(
    "value,status,code",
    [
        ({"mode": "confirm", "callers": ["规则", "AI"]}, 403, "CALLER_NOT_ALLOWED"),
        ({"mode": "suggest"}, 409, "ACTION_POLICY_SUGGEST_ONLY"),
    ],
)
async def test_caller_gate_and_suggest_create_no_run(runtime, client, value, status, code):  # noqa: F811
    session, action, _ = runtime
    await set_policy(session, action, value)
    before = await session.scalar(select(func.count()).select_from(ActionRun))
    result = await stage(client)
    assert result.status_code == status and result.json()["code"] == code, result.text
    assert await session.scalar(select(func.count()).select_from(ActionRun)) == before
