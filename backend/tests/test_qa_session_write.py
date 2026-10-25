"""W2 新建 / W4 重命名、改范围口径 / W5 删除会话 / W15 移出常用问题（工作台联调）。"""

import pytest

from app.seeds.loader import run_seeds

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    await run_seeds(users, ["qa"])
    await users.flush()
    return client


async def test_create_session_then_listed_first(qa):
    r = await qa.post(f"{B}/sessions", json={"scope": "华东", "caliber": "不含税"})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["title"] == "新会话" and s["turns"] == [] and s["caliber"] == "不含税"
    assert s["id"] == "S0104" and s["ownerId"] == "U010001"
    first = (await qa.get(f"{B}/sessions")).json()["items"][0]
    assert first["id"] == s["id"] and first["title"] == "新会话"


async def test_create_session_rejects_bad_caliber(qa):
    r = await qa.post(f"{B}/sessions", json={"caliber": "ex"})
    assert r.status_code == 422


async def test_rename_trims_and_blank_keeps(qa):
    r = await qa.patch(f"{B}/sessions/S0002", json={"title": "  客流 Top10  "})
    assert r.status_code == 200 and r.json()["title"] == "客流 Top10"
    assert r.json()["turns"] == []  # 只改标题不加系统轮次
    r = await qa.patch(f"{B}/sessions/S0002", json={"title": "   "})
    assert r.json()["title"] == "客流 Top10"


async def test_change_scope_caliber_appends_system_turn(qa):
    r = await qa.patch(f"{B}/sessions/S0001", json={"scope": "华东一区", "caliber": "含税"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["scope"] == "华东一区" and d["caliber"] == "含税"
    last = d["turns"][-1]
    assert last["kind"] == "system" and last["text"] == "从这里开始按 大区 = 华东一区、口径 = 含税 回答"
    n = len(d["turns"])
    # 值没变不再插入
    d2 = (await qa.patch(f"{B}/sessions/S0001", json={"scope": "华东一区"})).json()
    assert len(d2["turns"]) == n


async def test_delete_session(qa):
    assert (await qa.delete(f"{B}/sessions/S0001")).status_code == 204
    assert (await qa.get(f"{B}/sessions/S0001")).status_code == 404
    assert (await qa.delete(f"{B}/sessions/S0001")).status_code == 404


async def test_other_user_cannot_touch(qa):
    h = {"X-Demo-As": "U010006"}
    assert (await qa.patch(f"{B}/sessions/S0001", json={"title": "x"}, headers=h)).status_code == 404
    assert (await qa.delete(f"{B}/sessions/S0001", headers=h)).status_code == 404
    assert (await qa.delete(f"{B}/favorites/F001", headers=h)).status_code == 404


async def test_delete_favorite(qa):
    before = (await qa.get(f"{B}/favorites")).json()["total"]
    assert (await qa.delete(f"{B}/favorites/F001")).status_code == 204
    after = (await qa.get(f"{B}/favorites")).json()
    assert after["total"] == before - 1 and after["items"][0]["id"] == "F002"


async def test_writes_readonly_in_history(qa):
    r = await qa.post(f"{B}/sessions?ver=v2026.10.2", json={})
    assert r.status_code == 403 and r.json()["code"] == "READONLY"


async def test_ask_sql_exec_error_is_422_not_500(qa, session):
    """LLM 生成的 SQL 过了网关但执行报错（列不存在）→ 422 QA_SQL_FAILED，不是 500。"""
    from app.main import app
    from app.modules.qa import chain
    from app.modules.qa.llm import FakeLLM, get_llm

    async def _conn():
        conn = await session.connection()
        await conn.begin_nested()  # 执行报错只回滚到这个保存点，不污染测试事务
        yield conn

    app.dependency_overrides[chain.get_qa_conn] = _conn
    app.dependency_overrides[get_llm] = lambda: FakeLLM("select count(*) from product p where p.no_such_col")
    try:
        r = await qa.post(f"{B}/sessions/S0001/turns", json={"question": "有多少商品？"})
    finally:
        app.dependency_overrides.pop(chain.get_qa_conn, None)
        app.dependency_overrides.pop(get_llm, None)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "QA_SQL_FAILED"


async def _denied_turn(qa) -> str:
    turns = (await qa.get(f"{B}/sessions/S0001")).json()["turns"]
    return next(t["id"] for t in turns if t["kind"] == "denied")


async def test_permission_request_marks_turn_applied(qa):
    tid = await _denied_turn(qa)
    body = {
        "scope": "大区 = 华南",
        "object": "复购客户销售额（不含税） v3",
        "reason": " Q4 复购对比 ",
        "duration": 90,
        "sourceTurnId": tid,
    }
    r = await qa.post(f"{B}/permission-requests", json=body)
    assert r.status_code == 201, r.text
    assert r.json() == {"id": "AP0001", "status": "待审批", "approver": "刘洋（数据权限管理员）"}
    turn = next(t for t in (await qa.get(f"{B}/sessions/S0001")).json()["turns"] if t["id"] == tid)
    assert turn["applied"] is True
    # 只读白名单：历史版本下也能提交
    r = await qa.post(f"{B}/permission-requests?ver=v2026.10.2", json={**body, "sourceTurnId": None})
    assert r.status_code == 201 and r.json()["id"] == "AP0002"


async def test_permission_request_validation(qa):
    tid = await _denied_turn(qa)
    base = {"scope": "大区 = 华南", "object": "x", "reason": "r", "sourceTurnId": tid}
    assert (await qa.post(f"{B}/permission-requests", json={**base, "duration": 45})).status_code == 422
    assert (
        await qa.post(f"{B}/permission-requests", json={**base, "duration": 30, "reason": ""})
    ).status_code == 422
    r = await qa.post(
        f"{B}/permission-requests", json={**base, "duration": 30}, headers={"X-Demo-As": "U010006"}
    )
    assert r.status_code == 404
