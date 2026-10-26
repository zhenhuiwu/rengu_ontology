"""W2 新建 / W4 重命名、改范围口径 / W5 删除会话 / W15 移出常用问题（工作台联调）。"""

import json

import pytest
from sqlalchemy import func, select

from app.core.errors import AppError
from app.modules.qa.models import QaPermissionRequest, QaTurn
from tests.function_access_support import grant_fixture_roles
from tests.qa_access_support import seed_qa_access

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await grant_fixture_roles(users, {"U010001": "R-01", "U010006": "R-05"})
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


async def test_create_session_rejects_bad_caliber(qa, session):
    from app.modules.qa import service as svc

    idx = await svc.variant_index(session)  # 静态有口径变体才校验标签；超长总是 422
    r = await qa.post(f"{B}/sessions", json={"caliber": "ex"})
    assert r.status_code == (422 if idx else 201)
    assert (await qa.post(f"{B}/sessions", json={"caliber": "长" * 33})).status_code == 422


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


async def test_caliber_free_text_by_static_variants(qa, session):
    from app.modules.qa import chain
    from app.modules.qa import service as svc

    idx = await svc.variant_index(session)
    p = f"{B}/sessions/S0001"
    r = await qa.patch(p, json={"caliber": "  含税 "})  # 旧标签总是收；strip
    assert r.status_code == 200 and r.json()["caliber"] == "含税"
    d = (await qa.patch(p, json={"caliber": ""})).json()  # 空 → 不限
    assert d.get("caliber") is None and d["turns"][-1]["text"].endswith("口径 = 不限 回答")
    assert (await qa.patch(p, json={"caliber": "x" * 33})).status_code == 422
    r = await qa.patch(p, json={"caliber": "随便口径"})
    assert r.status_code == (422 if idx else 200)  # 静态有变体才校验
    if idx:
        label = next(iter(idx))
        assert (await qa.patch(p, json={"caliber": label})).json()["caliber"] == label
        lines = svc.caliber_lines(label, idx)
        assert all(v["api"] in "\n".join(lines) for v in idx[label])
    fake = {"零售价": [{"code": "M1", "name": "销售额（零售价）", "api": "gmv_retail", "version": "v1"}]}
    assert svc.check_caliber("零售价", fake) == "零售价" and svc.check_caliber("含税", fake) == "含税"
    with pytest.raises(AppError):
        svc.check_caliber("随便口径", fake)
    assert "`gmv_retail`" in "\n".join(svc.caliber_lines("零售价", fake))
    assert "含税口径" in svc.caliber_lines("含税", {})[0] and svc.caliber_lines(None, idx) == []
    msgs = chain.build_prompt("销售额", {"session_caliber": ["- 用户选定口径「含税」"]})
    assert "会话口径" in msgs[0]["content"] and "用户选定口径「含税」" in msgs[0]["content"]
    s = (await qa.post(f"{B}/sessions", json={"caliber": ""})).json()
    assert s.get("caliber") is None


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


async def test_permission_request_marks_turn_applied(qa, session):
    tid = await _denied_turn(qa)
    denied = await session.scalar(select(QaTurn).where(QaTurn.code == tid))
    denied.snapshot = {**denied.snapshot, "code": "OUT_OF_SCOPE"}
    await session.flush()
    body = {
        "scope": "大区 = 华南",
        "object": "复购客户销售额（不含税） v3",
        "reason": " Q4 复购对比 ",
        "duration": 90,
        "sourceTurnId": tid,
    }
    r = await qa.post(f"{B}/permission-requests", json=body)
    assert r.status_code == 201, r.text
    assert r.json() == {"id": "AP0001", "status": "待审批", "approver": None}
    turn = next(t for t in (await qa.get(f"{B}/sessions/S0001")).json()["turns"] if t["id"] == tid)
    assert turn["applied"] is True
    before = await session.scalar(select(func.count()).select_from(QaPermissionRequest))
    for query in ("ver=v2026.10.2", "previewAs=U010006"):
        r = await qa.post(f"{B}/permission-requests?{query}", json=body)
        assert r.status_code == 403 and r.json()["code"] == "READONLY"
    assert await session.scalar(select(func.count()).select_from(QaPermissionRequest)) == before
    stored = await session.scalar(select(QaPermissionRequest).where(QaPermissionRequest.code == "AP0001"))
    assert stored.approver is None and stored.reason == "Q4 复购对比"
    assert stored.source_turn_id == denied.id


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


async def test_permission_request_rejects_unencodable_and_blank_text(qa, session):
    body = {"scope": "请求范围", "object": "请求对象", "reason": "业务原因", "duration": 30}
    before = await session.scalar(select(func.count()).select_from(QaPermissionRequest))
    for field in ("scope", "object", "reason", "sourceTurnId"):
        for value in ("bad\x00text", "bad\ud800text"):
            response = await qa.post(
                f"{B}/permission-requests",
                content=json.dumps({**body, field: value}),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 422, (field, response.text)
            assert response.json()["code"] == "VALIDATION_FAILED"
    for field in ("scope", "object", "reason"):
        response = await qa.post(f"{B}/permission-requests", json={**body, field: "  "})
        assert response.status_code == 422
    assert await session.scalar(select(func.count()).select_from(QaPermissionRequest)) == before


async def test_caliber_index_uses_published_explicit_variants_and_historical_definition(users):
    from app.core import clock
    from app.modules.admin.models import SysUser
    from app.modules.qa import service as svc
    from app.modules.static.models import Metric, MetricStatus
    from app.modules.versioning.models import OntologyRelease

    def metric(code, name):
        return Metric(
            code=code,
            api_name=code,
            display_name=name,
            metric_group="测试",
            unit="元",
            version="v2",
            status=MetricStatus.published,
            owner="测试",
        )

    source = metric("M-variant-source", "来源指标")
    target = metric("M-variant-target", "真实目标指标")
    source.variants = [{"code": target.code, "label": "实际口径", "api": "STALE", "version": "v99"}]
    users.add_all([source, target])
    await users.flush()
    idx = await svc.variant_index(users)
    assert idx["实际口径"][0]["api"] == target.api_name
    assert idx["实际口径"][0]["version"] == "v2" and idx["实际口径"][0]["name"] == target.display_name
    with pytest.raises(AppError):
        svc.check_caliber("不存在口径", idx)
    target.status = MetricStatus.draft
    await users.flush()
    assert await svc.variant_index(users) == {}
    publisher = await users.scalar(select(SysUser.id).limit(1))
    users.add(
        OntologyRelease(
            ver="v2099.12.1",
            published_by_id=publisher,
            published_at=clock.now(),
            snapshot={
                "metrics": {
                    source.code: {
                        "id": source.code,
                        "api": source.api_name,
                        "name": "旧定义（含税）",
                        "version": "v1",
                        "status": "已发布",
                    }
                }
            },
            snapshot_sha256="0" * 64,
        )
    )
    await users.flush()
    assert await svc.variant_index(users, "v2099.12.1") == {}
