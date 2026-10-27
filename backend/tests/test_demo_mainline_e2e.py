"""演示主线（AL-20261025-0139 → 补货申请 → EXT-WEBHOOK → 决策记录）在真实种子上能走到哪一步。

真实种子到 dynamic + demo_mainline + integration_runs；EXT-WEBHOOK 适配器指向本测试起的 127.0.0.1 本地接收端
（不连外部主机、配置里没有任何密钥）。主线目前停在执行映射（ACTION_EDITS_UNAVAILABLE，动态/静态在查，
这里不修），所以断言接收端一次请求都没收到、没有生成外呼记录；哪天上游修好，本测试会失败提醒更新。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.seeds.loader import run_seeds
from tests.dynamic_support import API, dyn  # noqa: F401

ALERT, RUN = "AL-20261025-0139", "ACT-20261025-0552"
PLANNER = {"X-Demo-As": "U010011"}  # 周晓峰：告警处理人 / R-012 确认人


@pytest.fixture
def receiver():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            hits.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, hits
    server.shutdown()
    server.server_close()


@pytest.fixture
async def mainline(dyn, client, session, receiver, monkeypatch):  # noqa: F811
    from app.modules.integration.models import ExtSystem

    await run_seeds(session, ["demo_mainline", "integration_runs"])
    await session.commit()
    server, hits = receiver
    port = server.server_address[1]
    # 回滚事务里把演示通道指到本地接收端端口；适配器配置无 token / signing_secret
    await session.execute(
        text("UPDATE ext_system SET endpoint=:u WHERE code='EXT-WEBHOOK'"), {"u": f"https://127.0.0.1:{port}"}
    )
    await session.commit()
    settings = get_settings()
    monkeypatch.setattr(settings, "integration_real_connect", True)
    monkeypatch.setattr(
        settings,
        "integration_adapters",
        SecretStr(json.dumps({"1/EXT-WEBHOOK": {"submit_path": "/hook", "actions": ["request_replenish"]}})),
    )
    system = await session.scalar(select(ExtSystem).where(ExtSystem.code == "EXT-WEBHOOK"))
    return client, hits, system


async def _counts(session):
    return (
        await session.scalar(text("SELECT count(*) FROM action_run")),
        await session.scalar(text("SELECT count(*) FROM writeback_call")),
    )


async def test_mainline_stops_before_webhook(mainline, session):
    from app.modules.dynamic.models import DecisionLog
    from app.modules.integration.action_edits import execution_definition
    from app.modules.integration.execution import submit_adapter

    client, hits, system = mainline
    runs0, calls0 = await _counts(session)

    # 通道本身已就绪：补货申请定义绑定 EXT-WEBHOOK，本地适配器配置通过执行白名单 / 幂等头校验
    probe = SimpleNamespace(action_type_code="request_replenish", type_version="v3", sys_code="EXT-WEBHOOK")
    definition = await execution_definition(session, probe)
    assert definition["sysId"] == "EXT-WEBHOOK"
    assert submit_adapter(system, definition).config.actions == ["request_replenish"]

    # ① 既有暂存记录 ACT-0552：参数是自然语言快照，引用对象可见性无法核验 → 不能确认
    r = await client.get(f"{API}/action-runs/{RUN}", headers=PLANNER)
    assert r.status_code == 200, r.text
    run = r.json()
    assert (run["sysId"], run["approver"], run["status"]) == ("EXT-ERP", "品类经理 王敏", "待确认")
    assert run["paramsUnavailable"] == "参数不可用：无法确认全部引用对象的可见性"
    assert run["can"]["confirm"] is False
    r = await client.post(f"{API}/action-runs/{RUN}:confirm", headers=PLANNER, json={})
    assert r.status_code == 403 and r.json()["code"] == "ACTION_REFERENCE_UNAVAILABLE", r.text

    # ② 告警重新发起：认领后仍被既有关联动作挡住
    r = await client.post(f"{API}/alerts/{ALERT}:claim", headers=PLANNER)
    assert r.status_code == 200 and r.json()["status"] == "处理中", r.text
    r = await client.post(
        f"{API}/alerts/{ALERT}:start-action",
        headers=PLANNER,
        json={"params": {"product": "1011846", "qty": 205}},
    )
    assert r.status_code == 409 and r.json()["message"] == "该告警已有关联动作", r.text

    # ③ 新建手动补货申请：暂存时校验执行映射，ontology_edit_rule.field 是自然语言 → 停在这里
    r = await client.post(
        f"{API}/action-runs:stage",
        headers=PLANNER,
        json={
            "typeId": "request_replenish",
            "source": {"type": "手动"},
            "objKind": "product",
            "objId": "1011846",
            "objLabel": "伊利纯牛奶 250ml×16盒",
            "params": json.dumps(
                {"product": "1011846", "store": "SH0102", "qty": 205, "mode": "仓库调拨", "alert": ALERT},
                ensure_ascii=False,
            ),
        },
    )
    assert r.status_code == 503, r.text
    assert r.json()["code"] == "ACTION_EDITS_UNAVAILABLE"
    assert r.json()["message"] == "执行映射须为有界 JSON 对象，自然语言不能执行"

    # ④ 决策记录本身能写（只建议，不执行）；补货申请试运行未通过
    logs0 = await session.scalar(select(func.count()).select_from(DecisionLog))
    r = await client.post(f"{API}/alerts/{ALERT}/decision:evaluate", headers=PLANNER)
    assert r.status_code == 200, r.text
    decision = r.json()["decision"]
    assert decision["suggestOnly"] is True
    top = decision["recommendations"][0]
    assert top["actionType"]["id"] == "request_replenish" and top["dryRun"]["passed"] is False
    assert await session.scalar(select(func.count()).select_from(DecisionLog)) == logs0 + 1

    # 没有任何东西到达 Webhook
    assert hits == []
    assert await _counts(session) == (runs0, calls0)
