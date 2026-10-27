"""W7 流式（SSE）+ 动作意图 + 告警引用：FakeLLM，不调真实接口。"""

import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select, text

from app.db.session import get_session
from app.main import app
from app.modules.qa import chain, intent, stream
from app.modules.qa.llm import FakeLLM, get_llm
from app.modules.qa.models import QaTurn, QaTurnEvent
from tests.qa_access_support import seed_qa_access

SID = "S0001"
SQL = "```sql\nselect count(*) as \"门店数\" from store s where s.big_region = '华东';\n```"


def parse_sse(raw: str) -> list[dict]:
    out = []
    for block in raw.strip().split("\n\n"):
        ev = {"id": None, "event": None, "data": []}
        for line in block.splitlines():
            k, _, v = line.partition(": ")
            if k == "data":
                ev["data"].append(v)
            elif k in ("id", "event"):
                ev[k] = v
        ev["data"] = json.loads("\n".join(ev["data"]))
        out.append(ev)
    return out


@pytest.fixture
async def qa(users, client, session):
    await seed_qa_access(users)
    # 403 只读问题：本测试内把空间 1 的成员提成 editor（只在测试事务里，结束回滚）
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
        # 流式接口另有 request 作用域会话；conftest 的「每请求一个 savepoint」覆盖会被函数作用域那份先关掉
        # （生产里两份是不同会话），本文件改成直接给测试会话。
        yield session

    app.dependency_overrides[get_session] = _plain
    app.dependency_overrides[chain.get_qa_conn] = _conn
    app.dependency_overrides[chain.qa_conn_opener] = lambda: _open
    yield client
    for k in (chain.get_qa_conn, chain.qa_conn_opener, get_llm):
        app.dependency_overrides.pop(k, None)


def use_llm(reply: str) -> FakeLLM:
    fake = FakeLLM(reply)
    app.dependency_overrides[get_llm] = lambda: fake
    return fake


# ---------------------------------------------------------------- 纯函数


def test_sse_format_multiline_and_id():
    s = stream.sse("text", {"full": "a"}, 3)
    assert s == 'id: 3\nevent: text\ndata: {"full": "a"}\n\n'
    assert stream.sse("status", {"x": 1}).startswith("event: status\n")
    assert (
        stream.last_event_id("7") == 7 and stream.last_event_id("x") == 0 and stream.last_event_id(None) == 0
    )


def test_parse_intent_action_and_sql_like():
    kind, obj = chain.parse_intent('```json\n{"action": {"type": "A1", "objId": "S1"}}\n```')
    assert kind == "action" and obj["action"]["type"] == "A1"
    assert chain.sql_like("delete from store") and chain.sql_like("DROP TABLE x")
    assert not chain.sql_like("给华东门店发放优惠券")


def test_anchor_names():
    names = chain.anchor_names(["- store.name 最像的真实值：上海一店、杭州二店", "无关"])
    assert names == ["上海一店", "杭州二店"]


CATALOG = [
    {
        "id": "ACT01",
        "name": "发放优惠券",
        "targetKind": "store",
        "params": [{"api": "amount", "name": "金额", "type": "number", "required": True}],
        "policy": {"mode": "confirm", "aiAllowed": True},
    }
]


def test_parse_action_validates_type_and_params():
    a = intent.parse_action(
        {"action": {"type": "发放优惠券", "objId": "S1", "params": {"amount": 5, "evil": 1}, "reason": "r"}},
        CATALOG,
    )
    assert a["type"] == "ACT01" and a["params"] == {"amount": 5} and a["objKind"] == "store"
    assert intent.parse_action({"action": {"type": "NOPE"}}, CATALOG) is None
    assert intent.parse_action({"action": "x"}, CATALOG) is None


def test_action_snapshot_is_suggest_only():
    a = intent.parse_action(
        {"action": {"type": "ACT01", "objId": "S1", "objLabel": "一店", "params": {"amount": 5}}}, CATALOG
    )
    snap = intent.action_snapshot(
        a, {"passed": False, "checked": 1, "failed": [{"name": "金额", "msg": "超限"}]}
    )
    act = snap["answer"]["action"]
    assert snap["answer"]["card"] == "action" and act["mode"] == "suggest"
    assert act["dryRun"]["passed"] is False and "超限" in snap["answer"]["text"]
    assert act["stage"] == {
        "typeId": "ACT01",
        "objKind": "store",
        "objId": "S1",
        "objLabel": "一店",
        "params": "amount=5",
    }


async def test_dry_run_requires_target(users, session):
    a = {"type": "NO_SUCH", "name": "x", "params": {}, "objId": None}
    res = await intent.dry_run(session, None, a)
    assert res["passed"] is False and res["failed"]
    # 失败在 savepoint 里，外层事务还能用
    assert (await session.scalar(select(QaTurn.id).limit(1))) is None or True


# ---------------------------------------------------------------- 接口


async def test_w6_writes_events_and_replay(qa, session):
    use_llm(SQL)
    r = await qa.post(f"/api/qa/v1/sessions/{SID}/turns", json={"question": "华东大区有多少家门店？"})
    assert r.status_code == 202, r.text
    tid = r.json()["turnId"]
    t = await session.scalar(select(QaTurn).where(QaTurn.code == tid))
    names = (
        await session.scalars(
            select(QaTurnEvent.event_name).where(QaTurnEvent.turn_id == t.id).order_by(QaTurnEvent.seq)
        )
    ).all()
    assert names[0] == "meta" and "table" in names and names[-1] == "done"

    g = await qa.get(f"/api/qa/v1/turns/{tid}/stream")
    assert g.status_code == 200 and g.headers["content-type"].startswith("text/event-stream")
    evs = parse_sse(g.text)
    assert [e["event"] for e in evs] == names and evs[-1]["data"]["turn"]["id"] == tid
    # 续传：只给 Last-Event-ID 之后的
    g2 = await qa.get(f"/api/qa/v1/turns/{tid}/stream", headers={"Last-Event-ID": "1"})
    assert [e["id"] for e in parse_sse(g2.text)] == [str(i) for i in range(2, len(names) + 1)]


async def test_replay_unknown_turn_404(qa):
    r = await qa.get("/api/qa/v1/turns/NOPE-T001/stream")
    assert r.status_code == 404


async def test_live_stream(qa):
    use_llm(SQL)
    r = await qa.post(f"/api/qa/v1/sessions/{SID}/turns:stream", json={"question": "华东大区有多少家门店？"})
    assert r.status_code == 200, r.text
    evs = parse_sse(r.text)
    kinds = [e["event"] for e in evs]
    assert kinds[0] == "status" and "table" in kinds and kinds[-1] == "done"
    stages = [e["data"]["stage"] for e in evs if e["event"] == "status"]
    assert stages[:2] == ["understanding", "linking"] and "executing" in stages
    done = evs[-1]["data"]
    assert done["kind"] == "answer" and done["turn"]["answer"]["table"]["items"] == [{"c0": 95}]
    assert all(e["id"] for e in evs if e["event"] != "status")


async def test_live_stream_error_event(qa):
    use_llm(SQL)
    r = await qa.post("/api/qa/v1/sessions/S9999/turns:stream", json={"question": "x"})
    assert r.status_code == 404  # 流开始前的校验仍是普通错误


async def test_action_intent_without_catalog_refuses(qa):
    use_llm('```json\n{"action": {"type": "ACT01", "objId": "S1"}}\n```')
    r = await qa.post(f"/api/qa/v1/sessions/{SID}/turns", json={"question": "给上海一店发放优惠券"})
    assert r.status_code == 202, r.text
    turn = r.json()["turn"]
    assert turn["kind"] == "denied"


async def test_action_intent_with_catalog(qa, monkeypatch):
    async def fake_catalog(_s, _u, _r=None):
        return CATALOG

    async def fake_dry(_s, _u, a):
        return {"passed": True, "checked": 1, "failed": []}

    monkeypatch.setattr(intent, "action_catalog", fake_catalog)
    monkeypatch.setattr(intent, "dry_run", fake_dry)
    fake = use_llm(
        '```json\n{"action": {"type": "ACT01", "objId": "S1", "objLabel": "一店", '
        '"params": {"amount": 5}}}\n```'
    )
    r = await qa.post(f"/api/qa/v1/sessions/{SID}/turns", json={"question": "给上海一店发放优惠券"})
    assert r.status_code == 202, r.text
    ans = r.json()["turn"]["answer"]
    assert ans["action"]["mode"] == "suggest" and ans["action"]["stage"]["typeId"] == "ACT01"
    assert ans["action"]["policy"] == "confirm" and ans["action"]["canStage"] is True  # 无策略 = 默认 confirm
    assert "发放优惠券" in fake.prompts[0][0]["content"]


def test_action_snapshot_follows_policy():
    a = intent.parse_action({"action": {"type": "ACT01", "objId": "S1"}}, CATALOG)
    ok = {"passed": True, "checked": 1, "failed": []}
    sug = intent.action_snapshot(a, ok, {"mode": "suggest", "aiAllowed": True})
    assert sug["answer"]["action"]["canStage"] is False and sug["answer"]["action"]["policy"] == "suggest"
    assert "只给建议" in sug["answer"]["text"]
    auto = intent.action_snapshot(a, ok, {"mode": "auto", "aiAllowed": True})["answer"]["action"]
    assert auto["canStage"] is True and auto["policy"] == "auto" and auto["mode"] == "suggest"
    no_ai = intent.action_snapshot(a, ok, {"mode": "confirm", "aiAllowed": False})["answer"]["action"]
    assert no_ai["canStage"] is False


async def test_action_policy_defaults_to_confirm(users, session):
    assert await intent.action_policy(session, "NO_SUCH_ACTION") == {"mode": "confirm", "aiAllowed": True}
    assert intent.policy_view(None) == {"mode": "confirm", "aiAllowed": True}
    assert (
        intent.policy_view({"mode": "auto", "callers": ["人"], "limits": {"dailyCount": 1}})["aiAllowed"]
        is False
    )


async def test_action_catalog_comes_from_capabilities(users, session, monkeypatch):
    """候选动作 = 能力目录 actions 节（策略原样换算）；目录读失败 / 没有该节 → []，不自建目录。"""
    from app.modules.static import capabilities as caps

    seen = []

    async def fake_caps(_s, user, request=None, **_kw):
        seen.append(user)
        return {
            "actions": [
                {
                    "id": "A1",
                    "name": "调价",
                    "object": "store",
                    "rowAccess": "scoped",
                    "params": [{"api": "p", "name": "价", "type": "number", "required": 1}],
                    "policy": {"mode": "suggest"},
                },
                {
                    "id": "A2",
                    "name": "看不到",
                    "object": "store",
                    "rowAccess": "none",
                    "params": [],
                    "policy": {"mode": "confirm"},
                },
            ],
            "omitted": [],
        }

    monkeypatch.setattr(caps, "capabilities", fake_caps)
    out = await intent.action_catalog(session, "U")
    assert seen == ["U"] and [a["id"] for a in out] == ["A1"]
    assert (
        out[0]["policy"] == {"mode": "suggest", "aiAllowed": True} and out[0]["params"][0]["required"] is True
    )

    async def no_section(*_a, **_k):
        return {"omitted": [{"section": "actions"}]}

    monkeypatch.setattr(caps, "capabilities", no_section)
    assert await intent.action_catalog(session, "U") == []

    async def boom(*_a, **_k):
        raise RuntimeError("x")

    monkeypatch.setattr(caps, "capabilities", boom)
    assert await intent.action_catalog(session, "U") == []


async def _action_turn(qa, monkeypatch, mode: str) -> str:
    async def fake_catalog(_s, _u, _r=None):  # 卡片策略来自能力目录
        return [{**CATALOG[0], "policy": {"mode": mode, "aiAllowed": True}}]

    async def fake_dry(_s, _u, a):
        return {"passed": True, "checked": 1, "failed": []}

    async def fake_policy(_s, _code):
        return {"mode": mode, "aiAllowed": True}

    monkeypatch.setattr(intent, "action_catalog", fake_catalog)
    monkeypatch.setattr(intent, "dry_run", fake_dry)
    monkeypatch.setattr(intent, "action_policy", fake_policy)
    use_llm(
        '```json\n{"action": {"type": "ACT01", "objId": "S1", "objLabel": "一店", '
        '"params": {"amount": 5}}}\n```'
    )
    r = await qa.post(f"/api/qa/v1/sessions/{SID}/turns", json={"question": "给上海一店发放优惠券"})
    assert r.status_code == 202, r.text
    return r.json()["turn"]["id"]


async def test_stage_turn_action_never_auto_executes(qa, monkeypatch):
    from app.modules.dynamic.service import ActionService

    tid = await _action_turn(qa, monkeypatch, "auto")
    seen = []

    class _Run:
        code = "AR-QA-1"

    async def fake_stage(self, body):
        seen.append(body)
        return _Run(), True

    async def fake_detail(self, run):
        return {"id": run.code, "status": "待确认"}

    async def no_policy(self, run):  # auto 策略也不能走自动执行
        raise AssertionError("qa must not apply_policy")

    monkeypatch.setattr(ActionService, "stage", fake_stage)
    monkeypatch.setattr(ActionService, "detail", fake_detail)
    monkeypatch.setattr(ActionService, "apply_policy", no_policy)
    r = await qa.post(f"/api/qa/v1/turns/{tid}/action:stage", json={"objLabel": "上海一店"})
    assert r.status_code == 201, r.text
    assert r.json() == {"id": "AR-QA-1", "status": "待确认"}
    (body,) = seen
    assert body.type_id == "ACT01" and body.obj_id == "S1" and body.obj_label == "上海一店"
    assert json.loads(body.params) == {"amount": 5}  # 动作中心只认 JSON 对象参数
    assert body.source.type == "问数" and body.source.session == SID and body.source.confirmer


async def test_stage_turn_action_suggest_policy_409(qa, monkeypatch):
    tid = await _action_turn(qa, monkeypatch, "suggest")
    r = await qa.post(f"/api/qa/v1/turns/{tid}/action:stage")
    assert r.status_code == 409 and r.json()["code"] == "ACTION_POLICY_SUGGEST_ONLY"


async def test_stage_turn_action_needs_action_card(qa):
    use_llm(SQL)
    r = await qa.post(f"/api/qa/v1/sessions/{SID}/turns", json={"question": "华东门店数"})
    tid = r.json()["turn"]["id"]
    r = await qa.post(f"/api/qa/v1/turns/{tid}/action:stage")
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------- 告警引用（只读，走动态告警服务）


class _U:
    def __init__(self, func):
        self.func = func
        self.ontology_id = 1
        self.row_filters = {}


async def test_related_alerts_gated(users, session):
    assert intent.wants_alerts("最近有哪些告警") and not intent.wants_alerts("门店数")
    # 没提告警、没点名对象 → 不查
    assert await intent.related_alerts(session, _U({"alerts": "view"}), "门店数", []) == []
    # 没有告警页查看权限 → 不引用
    assert await intent.related_alerts(session, _U({}), "最近有哪些告警", []) == []


async def test_related_alerts_reads_via_service(qa, session, monkeypatch):
    from app.modules.dynamic import service as dyn

    seen = []

    async def fake_search(self, **kw):
        seen.append((kw["status"], kw["kw"]))

        class _P:
            items = []

        return _P()

    monkeypatch.setattr(dyn.AlertService, "search", fake_search)
    u = _U({"alerts": "view"})
    assert await intent.related_alerts(session, u, "上海一店销售额", ["上海一店"]) == []
    assert seen == [("open", "上海一店"), ("doing", "上海一店")]
