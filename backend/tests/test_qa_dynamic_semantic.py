"""batch 3：决策评估确认卡、单对象动态摘要、指标层（MetricFlow）问数。FakeLLM，不调真实模型。"""

from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from sqlalchemy import text

from app.common.metricflow_engine import MetricQueryInvalid
from app.db.session import get_session
from app.main import app
from app.modules.qa import chain, dynamic_ctx, intent, semantic
from app.modules.qa.llm import FakeLLM, get_llm
from tests.qa_access_support import seed_qa_access

SID = "S0001"
URL = f"/api/qa/v1/sessions/{SID}/turns"

# 通用形状的指标层导出（同 static /exports/metricflow）：一个模型、一个计数指标、一个派生分项
MANIFEST = {
    "semantic_models": [
        {
            "name": "store",
            "label": "门店",
            "node_relation": {"alias": "store", "schema_name": "public"},
            "defaults": {"agg_time_dimension": "opened_at"},
            "entities": [{"name": "store", "type": "primary", "expr": "code"}],
            "dimensions": [
                {"name": "big_region", "label": "大区", "expr": "big_region", "type": "categorical"},
                {"name": "opened_at", "label": "开业日期", "expr": "opened_at", "type": "time"},
            ],
            "measures": [
                {"name": "store_count", "agg": "count", "expr": "code", "agg_time_dimension": "opened_at"}
            ],
        }
    ],
    "metrics": [
        {
            "name": "store_count",
            "label": "门店数",
            "type": "simple",
            "type_params": {"measure": "store_count"},
            "config": {"meta": {"rengu": {"id": "M1", "unit": "家"}}},
        },
        {
            "name": "x__m1",
            "label": "分项",
            "type": "simple",
            "type_params": {"measure": "store_count"},
            "config": {"meta": {"rengu": {"id": "M2", "componentOf": "x"}}},
        },
    ],
}

ALERT = {
    "id": "AL0001",
    "rule": "R-STOCK",
    "level": "high",
    "objKind": "store",
    "objId": "SH0031",
    "objLabel": "上海一店",
    "value": 3,
    "threshold": 10,
    "status": "open",
    "triggeredAt": "2026-10-27T08:00:00",
}


def _rec(rank, type_id, name, params, unfilled=(), passed=True):
    return {
        "rank": rank,
        "score": 1.0 / rank,
        "actionType": {"id": type_id, "name": name, "version": 1, "targetKind": "store"},
        "reasons": [{"kind": "rule", "text": f"规则映射到{name}"}],
        "params": params,
        "paramSources": {k: "rule" for k in params},
        "unfilledParams": [{"api": a, "name": n, "type": "int"} for a, n in unfilled],
        "dryRun": {"ran": True, "passed": passed, "checked": 2, "failed": []},
        "availability": {"checked": False},
        "policy": {"mode": "confirm", "callerAllowed": True},
        "handoff": {
            "method": "POST",
            "path": "/api/ontology/v1/action-runs:stage",
            "requiresHumanConfirmation": True,
            "body": {
                "typeId": type_id,
                "source": "decision",
                "objKind": "store",
                "objId": "SH0031",
                "objLabel": "上海一店",
                "params": "{}",
            },
        },
    }


DECISION = {
    "decisionId": "D0001",
    "alertId": "AL0001",
    "objKind": "store",
    "objId": "SH0031",
    "recommendations": [
        _rec(1, "request_replenish", "补货申请", {"sku": "P001"}, unfilled=[("qty", "数量")]),
        _rec(2, "notify_manager", "通知店长", {}),
    ],
}


# ---------------------------------------------------------------- 纯函数


def test_wants_advice_and_target():
    assert dynamic_ctx.wants_advice("库存告警怎么处理") and not dynamic_ctx.wants_advice("门店数")
    obj = {"kind": "store", "id": "SH0031", "label": "上海一店"}
    # 问题点名告警编号
    assert dynamic_ctx.decision_target("AL0001 怎么办", [ALERT, {**ALERT, "id": "AL0002"}], None) == {
        "alertId": "AL0001"
    }
    # 点名对象只有一条告警
    assert dynamic_ctx.decision_target("上海一店怎么处理", [ALERT], obj) == {"alertId": "AL0001"}
    # 没告警 → 对象本身
    assert dynamic_ctx.decision_target("上海一店怎么处理", [], obj) == {
        "objectTypeId": "store",
        "objectId": "SH0031",
    }
    # 多条告警、没点名 → 动作意图的目标
    two = [ALERT, {**ALERT, "id": "AL0002"}]
    prop = {"objKind": "store", "objId": "SH0001"}
    assert dynamic_ctx.decision_target("怎么办", two, None, prop) == {
        "objectTypeId": "store",
        "objectId": "SH0001",
    }
    assert dynamic_ctx.decision_target("怎么办", two, None) is None


def test_decision_snapshot_ranked_card():
    snap = dynamic_ctx.decision_snapshot(DECISION)
    ans = snap["answer"]
    act = ans["action"]
    assert ans["card"] == "action" and act["mode"] == "suggest"
    assert act["type"] == "request_replenish" and act["stage"]["typeId"] == "request_replenish"
    assert act["stage"]["objId"] == "SH0031" and act["params"] == {"sku": "P001"}
    # qty 没填：校验不通过、提示待填，但仍可暂存由人补
    assert act["dryRun"]["passed"] is False and "数量" in act["dryRun"]["failed"][0]["msg"]
    assert act["canStage"] is True
    recs = act["decision"]["recommendations"]
    assert [r["actionType"]["id"] for r in recs] == ["request_replenish", "notify_manager"]
    assert act["decision"]["decisionId"] == "D0001" and "通知店长" in ans["text"]
    assert "decisions:evaluate" in ans["basis"]["caliber"]
    # 模型给了同一个动作和 qty：只补待填项，校验里去掉“数量待填”
    snap = dynamic_ctx.decision_snapshot(
        DECISION, {"type": "request_replenish", "params": {"qty": 20, "sku": "OTHER"}}
    )
    act = snap["answer"]["action"]
    assert act["params"] == {"sku": "P001", "qty": 20} and act["dryRun"]["passed"] is True
    # 模型选的是第二名 → 第二名为主建议
    act = dynamic_ctx.decision_snapshot(DECISION, {"type": "notify_manager", "params": {}})["answer"][
        "action"
    ]
    assert act["type"] == "notify_manager" and "补货申请" in act["reason"] + str(act["decision"])
    assert dynamic_ctx.has_type(DECISION, "notify_manager") and not dynamic_ctx.has_type(DECISION, "x")
    assert not dynamic_ctx.has_type(None, "x")


def test_decision_dry_run_not_ran():
    rec = _rec(1, "a", "动作A", {})
    rec["dryRun"] = {"ran": False, "reason": "no-permission"}
    act = dynamic_ctx.decision_snapshot({"recommendations": [rec]})["answer"]["action"]
    assert act["dryRun"]["passed"] is False and "未试运行" in act["dryRun"]["failed"][0]["msg"]


def test_summary_brief_and_text():
    raw = {
        "objKind": "store",
        "objKindLabel": "门店",
        "id": "SH0031",
        "objLabel": "上海一店",
        "alerts": {"available": True, "openCount": 2, "byStatus": {"open": 2}, "latest": [ALERT]},
        "ruleHits": {"available": True, "total": 5, "latest": []},
        "lifecycle": {"available": False, "reason": "no-permission"},
        "actionRuns": {"available": True, "total": 1, "statusCounts": {"done": 1}, "latest": []},
    }
    s = dynamic_ctx.brief(raw)
    assert s["alerts"]["openCount"] == 2 and s["lifecycle"] == {"available": False, "reason": "no-permission"}
    t = dynamic_ctx.summary_text(s)
    assert "上海一店" in t and "告警 2" in t and "规则命中 5" in t and "生命周期" not in t
    assert dynamic_ctx.summary_lines(s)[1].startswith("- 告警 AL0001")
    assert dynamic_ctx.summary_text(None) == ""


def test_semantic_lines_and_parse():
    lines = semantic.semantic_lines(MANIFEST)
    joined = "\n".join(lines)
    assert "指标 store_count：门店数（家）" in joined and "store__big_region（大区）" in joined
    assert "x__m1" not in joined  # 派生分项不列
    assert semantic.semantic_lines(None) == []
    kind, obj = chain.parse_intent('```json\n{"metric": {"metrics": ["store_count"]}}\n```')
    assert kind == "metric" and obj["metric"]["metrics"] == ["store_count"]


def test_semantic_plan_compiles():
    now = datetime(2026, 10, 27, 9, 30)  # noqa: DTZ001 - 同 clock.local_now 的本地时间
    p = semantic.plan(
        {
            "metric": {
                "metrics": ["store_count"],
                "dimensions": ["store__big_region"],
                "filters": [{"dimension": "store__big_region", "op": "=", "value": "华'东"}],
                "time": {"start": "2020-01-01", "end": "2030-01-01", "grain": "day"},
            }
        },
        MANIFEST,
        now,
    )
    assert p.group_by == ["store__big_region", "metric_time"]
    assert p.where[0] == "{{ Dimension('store__big_region') }} = '华''东'"
    assert p.where[-1].endswith("< '2026-10-27 09:30:00'")  # 上界不超过当前
    assert p.labels["store__big_region"] == "大区" and p.labels["metric_time"] == "日期"
    sql = semantic.compile_plan(MANIFEST, p)
    assert '"public"."store"' in sql and "CAST" in sql.upper()
    with pytest.raises(MetricQueryInvalid):
        semantic.plan({"metric": {"metrics": ["store_count"], "time": {"grain": "month"}}}, MANIFEST, now)
    with pytest.raises(MetricQueryInvalid):
        semantic.plan({"metric": {"metrics": ["store_count"], "filters": [{"op": "like"}]}}, MANIFEST, now)
    with pytest.raises(MetricQueryInvalid):  # 名字不对交给编译器
        semantic.compile_plan(MANIFEST, semantic.plan({"metric": {"metrics": ["nope"]}}, MANIFEST, now))


# ---------------------------------------------------------------- 链路


@pytest.fixture
async def qa(users, client, session):
    await seed_qa_access(users)
    await users.execute(
        text("UPDATE ontology_member SET role='editor' WHERE ontology_id=1 AND role='viewer'")
    )
    await users.flush()

    async def _conn():
        yield await session.connection()

    @asynccontextmanager
    async def _open(_oid):
        yield await session.connection()

    async def _plain():
        yield session

    app.dependency_overrides[get_session] = _plain
    app.dependency_overrides[chain.get_qa_conn] = _conn
    app.dependency_overrides[chain.qa_conn_opener] = lambda: _open
    yield client
    for k in (chain.get_qa_conn, chain.qa_conn_opener, get_llm, get_session):
        app.dependency_overrides.pop(k, None)


class SeqLLM(FakeLLM):
    def __init__(self, *replies: str) -> None:
        super().__init__(replies[-1])
        self.replies = list(replies)

    async def generate_sql(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def use_llm(*replies: str) -> FakeLLM:
    fake = SeqLLM(*replies)
    app.dependency_overrides[get_llm] = lambda: fake
    return fake


async def test_advice_question_uses_decisions(qa, monkeypatch):
    seen = {}

    async def fake_alerts(*_a, **_k):
        return [ALERT]

    async def fake_eval(_s, _u, target, caller, request=None):
        seen.update(target=target, caller=caller)
        return DECISION

    monkeypatch.setattr(intent, "related_alerts", fake_alerts)
    monkeypatch.setattr(dynamic_ctx, "evaluate", fake_eval)
    fake = use_llm("select 1")
    r = await qa.post(URL, json={"question": "库存告警怎么处理？"})
    assert r.status_code == 202, r.text
    body = r.json()
    ans = body["turn"]["answer"]
    assert ans["card"] == "action" and ans["action"]["type"] == "request_replenish"
    assert ans["action"]["decision"]["recommendations"][0]["rank"] == 1
    assert ans["alerts"][0]["id"] == "AL0001"
    assert seen["target"] == {"alertId": "AL0001"}
    # 调用方 = AI：会话号 + 预计轮次号（同落库的轮次编号）
    assert seen["caller"]["aiSessionId"] == SID and seen["caller"]["aiRoundId"] == body["turnId"]
    assert fake.prompts == []  # 确定性路径，不调模型


async def test_advice_falls_back_to_capabilities(qa, monkeypatch):
    async def fake_alerts(*_a, **_k):
        return [ALERT]

    async def no_eval(*_a, **_k):
        return None

    async def fake_catalog(_s, _u, _r=None):
        return [
            {
                "id": "ACT01",
                "name": "发放优惠券",
                "targetKind": "store",
                "params": [],
                "policy": {"mode": "confirm", "aiAllowed": True},
            }
        ]

    async def fake_dry(_s, _u, a):
        return {"passed": True, "checked": 1, "failed": []}

    monkeypatch.setattr(intent, "related_alerts", fake_alerts)
    monkeypatch.setattr(intent, "action_catalog", fake_catalog)
    monkeypatch.setattr(intent, "dry_run", fake_dry)
    monkeypatch.setattr(dynamic_ctx, "evaluate", no_eval)
    use_llm('```json\n{"action": {"type": "ACT01", "objKind": "store", "objId": "SH0031"}}\n```')
    r = await qa.post(URL, json={"question": "库存告警怎么处理？"})
    assert r.status_code == 202, r.text
    act = r.json()["turn"]["answer"]["action"]
    assert act["type"] == "ACT01" and "decision" not in act


async def test_llm_action_upgraded_by_decision(qa, monkeypatch):
    async def fake_catalog(_s, _u, _r=None):
        return [
            {
                "id": "request_replenish",
                "name": "补货申请",
                "targetKind": "store",
                "params": [{"api": "qty", "name": "数量", "type": "number", "required": True}],
                "policy": {"mode": "confirm", "aiAllowed": True},
            }
        ]

    async def fake_eval(_s, _u, target, caller, request=None):
        assert target == {"objectTypeId": "store", "objectId": "SH0031"}
        return DECISION

    rechecked = []

    async def fake_dry_run(_s, _u, chosen):
        rechecked.append(chosen["params"])
        return {"ran": True, "passed": True, "checked": [], "failed": []}

    monkeypatch.setattr(intent, "action_catalog", fake_catalog)
    monkeypatch.setattr(intent, "dry_run", fake_dry_run)
    monkeypatch.setattr(dynamic_ctx, "evaluate", fake_eval)
    use_llm(
        '```json\n{"action": {"type": "request_replenish", "objKind": "store", "objId": "SH0031", '
        '"params": {"qty": 30}}}\n```'
    )
    r = await qa.post(URL, json={"question": "给 SH0031 发起补货"})
    assert r.status_code == 202, r.text
    act = r.json()["turn"]["answer"]["action"]
    assert act["decision"]["decisionId"] == "D0001" and act["params"]["qty"] == 30
    assert rechecked == [{"sku": "P001", "qty": 30}] and act["dryRun"]["passed"] is True


async def test_single_object_summary_attached(qa, session, monkeypatch):
    code = (await session.execute(text("select code, name from store order by code limit 1"))).one()
    assert await dynamic_ctx.single_object(session, f"{code[0]} 的情况", {}) == {
        "kind": "store",
        "id": code[0],
        "label": code[1],
    }
    assert await dynamic_ctx.single_object(session, "门店数", {}) is None

    async def fake_summary(_s, _u, obj, request=None):
        return dynamic_ctx.brief(
            {
                "objKind": obj["kind"],
                "objKindLabel": "门店",
                "id": obj["id"],
                "objLabel": obj["label"],
                "alerts": {"available": True, "openCount": 1, "latest": []},
                "ruleHits": {"available": False, "reason": "no-permission"},
                "lifecycle": {"available": False, "reason": "no-permission"},
                "actionRuns": {"available": False, "reason": "no-permission"},
            }
        )

    monkeypatch.setattr(dynamic_ctx, "object_summary", fake_summary)
    fake = use_llm(f"select name from store where code = '{code[0]}'")
    r = await qa.post(URL, json={"question": f"{code[0]} 叫什么"})
    assert r.status_code == 202, r.text
    ans = r.json()["turn"]["answer"]
    assert ans["dynamicSummary"]["id"] == code[0] and "未关闭告警 1 条" in ans["text"]
    assert "动态摘要" in fake.prompts[0][0]["content"]


async def test_multi_object_summary_top_with_alerts(qa, session, monkeypatch):
    rows = (await session.execute(text("select code, name from store order by code limit 3"))).all()
    codes = [r[0] for r in rows]
    open_by = {codes[0]: 1, codes[1]: 0, codes[2]: 4}  # 没告警的不列，告警多的在前

    async def fake_summary(_s, _u, obj, request=None):
        if not obj:
            return None
        return dynamic_ctx.brief(
            {
                "objKind": obj["kind"],
                "objKindLabel": "对象",
                "id": obj["id"],
                "objLabel": obj["label"],
                "alerts": {"available": True, "openCount": open_by[obj["id"]], "latest": []},
                "ruleHits": {"available": False, "reason": "no-permission"},
                "lifecycle": {"available": False, "reason": "no-permission"},
                "actionRuns": {"available": False, "reason": "no-permission"},
            }
        )

    monkeypatch.setattr(dynamic_ctx, "object_summary", fake_summary)
    monkeypatch.setattr("app.modules.qa.object_source.check_object_read", lambda _u: None)
    in_list = ", ".join(f"'{c}'" for c in codes)
    use_llm(f"select code, name from store where code in ({in_list}) order by code")
    r = await qa.post(URL, json={"question": "这几家的名称"})
    assert r.status_code == 202, r.text
    ans = r.json()["turn"]["answer"]
    assert [s["id"] for s in ans["dynamicSummaries"]] == [codes[2], codes[0]]
    assert "dynamicSummary" not in ans and "动态提示" in ans["text"] and "未关闭告警 4 条" in ans["text"]
    # 可问表在第一次请求时注册（单独跑本用例时也一样）
    named = await dynamic_ctx.named_objects(session, f"{codes[0]} 和 {codes[1]} 对比", {})
    assert {o["id"] for o in named} == set(codes[:2])
    assert await dynamic_ctx.single_object(session, f"{codes[0]} 和 {codes[1]} 对比", {}) is None
    found = await dynamic_ctx.result_objects(session, [[c, 1] for c in codes] + [["不是编号", 2]])
    assert [o["id"] for o in found] == codes
    # 问题点名多个对象：进 prompt
    fake = use_llm(f"select code, name from store where code in ('{codes[0]}', '{codes[2]}')")
    r = await qa.post(URL, json={"question": f"{codes[0]} 和 {codes[2]} 的名称"})
    assert r.status_code == 202, r.text
    assert len(r.json()["turn"]["answer"]["dynamicSummaries"]) == 2
    assert "动态摘要" in fake.prompts[0][0]["content"]
    assert dynamic_ctx.multi_text([]) == ""


async def test_object_summary_gated(session):
    class _U:
        func = {}

    assert await dynamic_ctx.object_summary(session, _U(), {"kind": "store", "id": "X"}) is None
    assert await dynamic_ctx.object_summary(session, _U(), None) is None


async def test_metric_intent_runs_engine(qa, monkeypatch):
    async def layer(*_a, **_k):
        return MANIFEST

    monkeypatch.setattr(semantic, "metric_layer", layer)
    fake = use_llm(
        '```json\n{"metric": {"metrics": ["store_count"], "filters": '
        '[{"dimension": "store__big_region", "op": "=", "value": "华东"}]}}\n```'
    )
    r = await qa.post(URL, json={"question": "华东大区有多少家门店？"})
    assert r.status_code == 202, r.text
    ans = r.json()["turn"]["answer"]
    assert ans["table"]["columns"][0]["title"] == "门店数" and ans["table"]["items"] == [{"c0": 95}]
    assert ans["basis"]["caliber"] == semantic.CALIBER
    assert ans["metricQuery"]["metrics"] == [{"name": "store_count", "label": "门店数"}]
    prompt = fake.prompts[0][0]["content"]
    assert "指标层" in prompt and "store__big_region" in prompt


async def test_metric_intent_falls_back_to_sql(qa, monkeypatch):
    async def layer(*_a, **_k):
        return MANIFEST

    monkeypatch.setattr(semantic, "metric_layer", layer)
    fake = use_llm(
        '```json\n{"metric": {"metrics": ["store_count"], "time": {"grain": "month"}}}\n```',
        "select count(*) as \"门店数\" from store s where s.big_region = '华东'",
    )
    r = await qa.post(URL, json={"question": "华东大区有多少家门店？"})
    assert r.status_code == 202, r.text
    ans = r.json()["turn"]["answer"]
    assert ans["table"]["items"] == [{"c0": 95}] and "metricQuery" not in ans
    assert "指标层编译不了" in fake.prompts[1][-1]["content"]


HUMAN = [{"name": "资料齐全", "rule": "附件需包含签字版", "reason": "自然语言规则"}]
DECLARED = [
    {"kind": "编辑", "op": "修改", "obj": "工单", "field": "状态、处理人", "status": "external/declared"},
    {"kind": "通知", "target": "外部群", "when": "提交后", "note": "通知值班", "status": "external/declared"},
]


def test_action_card_shows_human_checks_and_declared_edits():
    intent_ = {
        "type": "ACT9",
        "name": "外部处理",
        "objKind": "ticket",
        "objId": "T1",
        "objLabel": "T1",
        "params": {},
    }
    check = {"passed": True, "checked": 2, "failed": [], "humanChecks": HUMAN, "declaredEdits": DECLARED}
    act = intent.action_snapshot(intent_, check)["answer"]
    a = act["action"]
    assert a["dryRun"]["humanChecks"] == HUMAN and a["humanChecks"] == HUMAN
    assert a["dryRun"]["passed"] is True and a["dryRun"]["declaredEdits"] == DECLARED
    assert "1 项提交条件需确认人人工核对（资料齐全）" in act["text"]
    assert "由外部系统承担" in act["text"] and "工单 状态、处理人" in a["declaredSummary"]
    # 决策评估的试运行结果：humanChecks 原样带上，不算失败
    rec_check = dynamic_ctx._check_of(
        {"dryRun": {"ran": True, "passed": True, "checked": 1, "humanChecks": HUMAN}}
    )
    assert rec_check["passed"] and rec_check["humanChecks"] == HUMAN
    plain = intent.action_snapshot(intent_, {"passed": True, "checked": 1, "failed": []})["answer"]
    assert "humanChecks" not in plain["action"] and "declaredSummary" not in plain["action"]


async def test_with_declared_fills_decision_cards(monkeypatch):
    async def fake_declared(_s, type_id):
        return DECLARED if type_id == "ACT9" else []

    monkeypatch.setattr(intent, "declared_edits", fake_declared)
    snap = {"answer": {"text": "建议。", "action": {"type": "ACT9", "dryRun": {"passed": True}}}}
    out = await intent.with_declared(None, snap)
    assert out["answer"]["action"]["dryRun"]["declaredEdits"] == DECLARED
    assert out["answer"]["text"].startswith("建议。由外部系统承担")
    other = {"answer": {"text": "x", "action": {"type": "ACT1", "dryRun": {}}}}
    assert "declaredEdits" not in (await intent.with_declared(None, other))["answer"]["action"]["dryRun"]
