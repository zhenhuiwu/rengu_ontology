"""演示主线（AL-20261025-0139 → 补货申请 → EXT-WEBHOOK → 决策记录）的种子前置条件。

完整走通的流程（认领 → 评估 → 告警发起 → U010812 审批 → 签名 Webhook → 已对账 → 决策日志）
由 tests/test_palantir_mainline.py 覆盖，这里不重复跑，只锁住 demo_mainline 种子给主线准备的状态：
旧暂存动作 ACT-20261025-0552 已作废且不再挂在告警上，补货申请走 EXT-WEBHOOK、审批人是 姚娜。
"""

from types import SimpleNamespace

import pytest

from app.seeds.loader import run_seeds
from tests.dynamic_support import API, dyn  # noqa: F401

ALERT, RUN = "AL-20261025-0139", "ACT-20261025-0552"
PLANNER = {"X-Demo-As": "U010011"}  # 周晓峰：告警处理人 / R-012 确认人


@pytest.fixture
async def mainline(dyn, client, session):  # noqa: F811
    await run_seeds(session, ["demo_mainline", "integration_runs"])
    await session.commit()
    return client


async def test_mainline_preconditions(mainline, session):
    from app.modules.integration.action_edits import execution_definition

    client = mainline
    probe = SimpleNamespace(action_type_code="request_replenish", type_version="v3", sys_code="EXT-WEBHOOK")
    definition = await execution_definition(session, probe)
    assert definition["sysId"] == "EXT-WEBHOOK"

    # 旧记录作废：已撤回，不能再确认
    r = await client.get(f"{API}/action-runs/{RUN}", headers=PLANNER)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "已撤回" and r.json()["can"]["confirm"] is False, r.json()

    # 告警不再关联旧动作：待处理、处理人 U010011，可认领后重新发起
    r = await client.get(f"{API}/alerts/{ALERT}", headers=PLANNER)
    assert r.status_code == 200, r.text
    alert = r.json()
    assert not alert.get("actionId") and not alert.get("action"), alert
    assert alert["status"] == "待处理"
    r = await client.post(f"{API}/alerts/{ALERT}:claim", headers=PLANNER)
    assert r.status_code == 200 and r.json()["status"] == "处理中", r.text
