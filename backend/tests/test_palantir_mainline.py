"""演示主线（t7）：外部 Webhook 动作的自然语言编辑 = 声明效果，自然语言条件 = 确认人人工核对。

真实种子 demo_mainline + integration_runs；EXT-WEBHOOK 由本测试内的本地接收端（httpx MockTransport，
不出网）承接并校验 HMAC 签名。签名密钥只在本测试的适配器配置里，不改种子。
"""

import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.core.config import get_settings
from app.seeds.loader import run_seeds
from tests.dynamic_support import API, dyn  # noqa: F401
from tests.test_palantir_webhook import scope_of

ALERT = "AL-20261025-0139"
PLANNER = {"X-Demo-As": "U010011"}  # 告警处理人 / 手动发起人 = 确认人
APPROVER = {"X-Demo-As": "U010812"}  # 演示审批人
SECRET = "palantir-t7-test-only-secret"
STAGE = {
    "typeId": "request_replenish",
    "source": {"type": "手动"},
    "objKind": "product",
    "objId": "1011846",
    "objLabel": "伊利纯牛奶 250ml×16盒",
    "idempotencyKey": "palantir-t7-mainline",
    "params": json.dumps({"product": "1011846", "store": "SH0102", "qty": 143, "mode": "仓库调拨", "alert": ALERT}),
}


@pytest.fixture
async def mainline(dyn, client, session, monkeypatch):  # noqa: F811
    from app.modules.integration import execution
    from app.modules.integration.signing import SIGNATURE_HEADER, TIMESTAMP_HEADER, verify

    await run_seeds(session, ["demo_mainline", "integration_runs"])
    await session.commit()
    hits = []

    def receive(request: httpx.Request):  # 本地接收端：验签 + 回执
        body = request.read()
        hits.append({"url": str(request.url), "body": json.loads(body), "valid": verify(
            SECRET.encode(), request.headers[TIMESTAMP_HEADER], body, request.headers[SIGNATURE_HEADER]),
            "idem": request.headers.get("Idempotency-Key")})
        return httpx.Response(200, json={"status": "SUCCESS", "receiptId": "MOCK-RCPT-1"})

    real = httpx.AsyncClient

    def client_factory(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return real(transport=httpx.MockTransport(receive), timeout=kwargs.get("timeout"))

    monkeypatch.setattr(execution.httpx, "AsyncClient", client_factory)
    settings = get_settings()
    monkeypatch.setattr(settings, "integration_real_connect", True)
    monkeypatch.setattr(settings, "integration_adapters", SecretStr(json.dumps({"1/EXT-WEBHOOK": {
        "submit_path": "/hook", "actions": ["request_replenish"], "signing_secret": SECRET}})))
    return client, hits


async def test_mainline_direct_stage_reaches_signed_webhook(mainline, session):
    from app.modules.integration import execution

    client, hits = mainline
    claim = await client.post(f"{API}/alerts/{ALERT}:claim", headers=PLANNER)
    assert claim.status_code in (200, 409), claim.text
    evaluated = await client.post(f"{API}/alerts/{ALERT}/decision:evaluate", headers=PLANNER)
    assert evaluated.status_code == 200, evaluated.text
    top = evaluated.json()["decision"]["output"]["recommendations"][0] if "output" in evaluated.json()["decision"] \
        else evaluated.json()["decision"]["recommendations"][0]
    assert top["actionType"]["id"] == "request_replenish"
    assert top["availability"]["available"] is True, top["availability"]
    assert top["dryRun"]["passed"] is True and top["dryRun"]["humanChecks"], top["dryRun"]

    staged = await client.post(f"{API}/action-runs:stage", json=STAGE, headers=PLANNER)
    assert staged.status_code == 201, staged.text
    run = staged.json()
    code = run["id"]
    detail = (await client.get(f"{API}/action-runs/{code}", headers=PLANNER)).json()
    assert detail["paramsUnavailable"] is None and detail["can"]["confirm"] is True, detail["can"]
    texts = [log["text"] for log in detail["log"]]
    assert any(t.startswith("需确认人人工核对的提交条件") for t in texts), detail["log"]

    confirmed = await client.post(f"{API}/action-runs/{code}:confirm", headers=PLANNER)
    assert confirmed.status_code == 200, confirmed.text
    approved = await client.post(f"{API}/action-runs/{code}:approve", headers=APPROVER)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "结果未知"  # 已持久排队，等待执行器
    call_id = await session.scalar(text(
        "SELECT c.id FROM writeback_call c JOIN action_run r ON r.id = c.action_run_id WHERE r.code = :c"), {"c": code})
    with patch.object(execution, "scoped_sessions", scope_of(session)):  # 执行器一次（测试事务内，不起 worker）
        await execution.dispatch_one(call_id, 1)
    assert len(hits) == 1, hits
    hit = hits[0]
    assert hit["url"].endswith("/hook") and hit["valid"] is True
    assert hit["body"]["actionRunId"] == code and hit["body"]["actionType"]
    declared = hit["body"]["declaredEdits"]
    assert declared and {d["status"] for d in declared} == {"external/declared"}
    assert {d["kind"] for d in declared} >= {"编辑"}
    final = await client.get(f"{API}/action-runs/{code}", headers=PLANNER)
    body = final.json()
    # 排队执行的结果经对账任务落定：外部已成功 + 回执单号
    assert final.status_code == 200 and body["status"] == "已对账" and body["extRef"] == "MOCK-RCPT-1", body
    print("FINAL", body["status"], body["reconcile"], [x["text"] for x in body["log"]][-4:])

    logs = await client.get(f"{API}/decisions", params={"alertId": ALERT}, headers=PLANNER)
    assert logs.status_code == 200 and logs.json()["total"] >= 1, logs.text
    print("STEPS", claim.status_code, evaluated.status_code, staged.status_code, code, confirmed.json()["status"],
          approved.json()["status"], hit["valid"], final.json()["status"], logs.json()["total"])
    assert await session.scalar(text("SELECT http_status FROM writeback_call WHERE id = :i"), {"i": call_id}) == 200


async def test_declared_edits_only_on_external_webhook_and_json_stays_strict(mainline, session):
    from app.common.auth import resolve_user
    from app.common.context import ViewContext
    from app.core.errors import AppError
    from app.modules.action.checker import DatabaseSubmissionChecker, definition
    from app.modules.integration.action_edits import business_edits, declared_writeback

    user = await resolve_user(session, "U010011")
    action = await definition(session, "request_replenish", ViewContext(), "published", None)
    assert action["sysId"] == "EXT-WEBHOOK"
    declared = await declared_writeback(session, action)
    assert {d["kind"] for d in declared} >= {"编辑"} and all(d["status"] == "external/declared" for d in declared)
    run = type("Run", (), {"code": "DRY", "obj_kind": "product", "obj_id": "1011846", "type_version": "v3",
                           "params": {"product": "1011846"}, "params_text": None})()
    await business_edits(session, user, run, action)  # 自然语言编辑：外部承担，本地不执行、不报错

    local = {**action, "sysId": None}  # 无外部 Webhook 通道：仍严格拒绝自然语言
    assert await declared_writeback(session, local) == []
    with pytest.raises(AppError) as error:
        await business_edits(session, user, run, local)
    assert error.value.code == "ACTION_EDITS_UNAVAILABLE"
    result = await DatabaseSubmissionChecker(session, user).run(
        local, {"objKind": "product", "objId": "1011846", "params": run.params})
    assert result["passed"] is False and "humanChecks" not in result

    bad_json = {**action, "writeback": {**action["writeback"], "edits": [
        {"op": "修改", "obj": "商品", "field": json.dumps({"values": {"status": {"value": "x"}}})}]}}
    with pytest.raises(AppError) as error:  # 有界 JSON 映射在外部通道上也保持严格校验
        await business_edits(session, user, run, bad_json)
    assert error.value.code == "ACTION_EDITS_UNAVAILABLE"


async def test_human_checks_force_confirm_in_auto_mode(mainline, monkeypatch):
    from types import SimpleNamespace

    from app.modules.action import policy as action_policy
    from app.modules.dynamic.models import ActionStatus
    from app.modules.dynamic.service import ActionService

    async def auto(*args):
        return SimpleNamespace(mode="auto")

    async def never(*args, **kwargs):
        raise AssertionError("含人工核对条件时不能进入自动执行判定")

    logs = []

    async def log(r, text, desc=None, level=None):
        logs.append(text)

    monkeypatch.setattr(action_policy, "load", auto)
    monkeypatch.setattr(action_policy, "auto_decision", never)
    svc = ActionService.__new__(ActionService)
    svc.session, svc._human_runs, svc._log = None, {"ACT-H"}, log
    run = SimpleNamespace(code="ACT-H", action_type_code="request_replenish", type_version="v3",
                          status=ActionStatus.staged)
    assert await svc.apply_policy(run) is run and run.status == ActionStatus.staged
    assert svc.policy_mode == "auto" and "人工核对" in svc.policy_overflow and logs
