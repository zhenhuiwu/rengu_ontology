"""tier-1 演示的问数段（真实种子 + 真实动态服务，FakeLLM）：

库存告警 → 问「怎么处理」→ 决策评估（decisions:evaluate，进程内，记 decision_log，调用方 = AI + 本轮编号）
→ 确认卡（补货申请排第一，数量可空）→ `POST /turns/{id}/action:stage` 暂存成动作中心待确认（不执行）。
"""

import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select, text

from app.db.session import get_session
from app.main import app
from app.modules.qa import chain
from app.modules.qa.llm import FakeLLM, get_llm
from app.seeds.loader import run_seeds
from tests.dynamic_support import API, dyn  # noqa: F401

SID = "S0001"


@pytest.fixture
async def demo(dyn, client, session):  # noqa: F811
    await run_seeds(session, ["integration_runs", "qa"])
    await session.execute(
        text("UPDATE ontology_member SET role='editor' WHERE ontology_id=1 AND role='viewer'")
    )
    # 演示环境要先给补货申请配好审批人（种子里是一段说明文字 → approvalConfigured=False，动态种子测试有断言）
    await session.execute(text("UPDATE action_type SET approval='U010001' WHERE code='request_replenish'"))
    await session.flush()

    async def _conn():
        yield await session.connection()

    @asynccontextmanager
    async def _open(_oid):
        yield await session.connection()

    async def _plain():
        yield session

    llm = FakeLLM("```sql\nSELECT 1\n```")
    app.dependency_overrides[get_session] = _plain
    app.dependency_overrides[chain.get_qa_conn] = _conn
    app.dependency_overrides[chain.qa_conn_opener] = lambda: _open
    app.dependency_overrides[get_llm] = lambda: llm
    yield client
    for k in (chain.get_qa_conn, chain.qa_conn_opener, get_llm, get_session):
        app.dependency_overrides.pop(k, None)


async def test_alert_to_decision_card_to_staged_run(demo, session):
    from app.modules.dynamic.models import ActionRun, DecisionLog

    alerts = (await demo.get(f"{API}/alerts", params={"status": "all", "ruleId": "R-012", "size": 1})).json()
    alert = alerts["items"][0]
    r = await demo.post(
        f"/api/qa/v1/sessions/{SID}/turns", json={"question": f"告警 {alert['id']} 该怎么处理？"}
    )
    assert r.status_code == 202, r.text
    turn = r.json()["turn"]
    act = turn["answer"]["action"]
    assert turn["answer"]["card"] == "action", turn["answer"]
    assert act["type"] == "request_replenish"  # 补货申请排第一
    d = act["decision"]
    assert d["alertId"] == alert["id"] and d["recommendations"][0]["actionType"]["id"] == "request_replenish"
    assert "决策评估" in turn["answer"]["basis"]["caliber"]

    log = await session.scalar(select(DecisionLog).where(DecisionLog.code == d["decisionId"]))
    assert log is not None and log.caller_kind == "ai"
    assert log.ai_session_id == SID and log.ai_round_id == turn["id"]  # 调用方 = AI + 本轮编号

    params = dict(act["params"])
    for p in d["recommendations"][0]["unfilledParams"]:  # 人工确认时补齐待填参数（如数量）
        params[p["api"]] = 10
    url = f"/api/qa/v1/turns/{turn['id']}/action:stage"

    # 1) 暂存请求体：来源 问数 + 本会话 + 告警；参数是动作中心认的 JSON 对象（不是「k=v」摘要）
    from app.modules.dynamic.service import ActionService

    seen = []

    class _Run:
        code = "AR-QA-DEMO"

    async def fake_stage(self, body):
        seen.append(body)
        return _Run(), True

    async def fake_detail(self, run):
        return {"id": run.code, "status": "待确认"}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ActionService, "stage", fake_stage)
        mp.setattr(ActionService, "detail", fake_detail)
        r = await demo.post(url, json={"params": json.dumps(params)})
    assert r.status_code == 201, r.text
    (body,) = seen
    assert body.type_id == "request_replenish" and body.obj_id == act["stage"]["objId"]
    assert body.source.type == "问数" and body.source.session == SID and body.source.alert_id == alert["id"]
    assert json.loads(body.params) == params

    # 2) 真实动作中心：该告警已有补货申请记录（规则自动暂存 / 种子）→ 不报幂等冲突，返回那条让人去确认
    existing = await session.scalar(
        select(ActionRun).where(ActionRun.idem_key == f"act-{alert['id'].lower()}").limit(1)
    )
    before = await session.scalar(select(func.count(ActionRun.id)))
    r = await demo.post(url, json={"params": json.dumps(params)})
    assert existing is not None and existing.action_type_code == "request_replenish"  # 种子：R-012 已自动暂存
    assert r.status_code == 200, r.text
    assert r.json()["id"] == existing.code
    assert await session.scalar(select(func.count(ActionRun.id))) == before  # 不新建、不执行
