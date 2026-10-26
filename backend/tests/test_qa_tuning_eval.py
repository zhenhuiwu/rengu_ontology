"""T11 运行评测（FakeLLM 直接调 job.execute）/ T15 转同义词 / T16 转认证 / T17 忽略与重新打开。"""

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.main import app
from app.modules.qa import catalog
from app.modules.qa.eval import job
from app.modules.qa.llm import FakeLLM, get_llm
from app.modules.qa.models import FailKind, QaCert, QaEvalQuestion, QaEvalRun
from app.modules.static.models import Metric
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.service import VersioningService
from tests.function_access_support import grant_fixture_roles
from tests.qa_access_support import seed_qa_access

B = "/api/qa/v1/tuning"


async def test_catalog_empty_has_no_seed_metadata(users, client):
    await grant_fixture_roles(users, {"U010001": "R-01"})
    response = await client.get(f"{B}/plan-options")
    assert response.status_code == 200
    assert response.json()["metrics"] == [] and response.json()["dims"] == []
    assert (await client.get("/api/qa/v1/session-options")).json() == {
        "scopes": [],
        "calibers": [],
        "metric": {},
    }
    response = await client.post(
        f"{B}/certs", json={"question": "缺失指标", "plan": {"metric": "orders", "dims": [], "time": "上周"}}
    )
    assert response.status_code == 422
    q = QaEvalQuestion(expect={"metric": "orders", "dims": []})
    result = job.judge(q, "answer", {"answer": {"table": {"columns": [{"title": "有效订单数"}]}}}, {})
    assert result[0] == FailKind.metric_not_recalled and "不可用" in result[1]


async def test_catalog_database_labels_aliases_and_new_code(qa, session):
    m = await session.scalar(select(Metric).where(Metric.code == "order_cnt"))
    m.display_name, m.version = "数据库订单指标", "v9"
    session.add(
        Metric(
            code="M01",
            api_name="actual_metric",
            display_name="数据库新增指标",
            metric_group="销售",
            unit="个",
            version="口径A",
            status="published",
            owner="测试",
            seq=99,
        )
    )
    await session.flush()
    options = (await qa.get(f"{B}/plan-options")).json()
    by_key = {x["key"]: x for x in options["metrics"]}
    assert by_key["orders"]["name"] == "数据库订单指标"
    assert by_key["orders"]["version"] == "v9" and by_key["orders"]["code"] == "order_cnt"
    assert by_key["M01"]["name"] == "数据库新增指标"
    plan = {"metric": "order_cnt", "dims": [], "time": "上周"}
    response = await qa.post(f"{B}/certs", json={"question": "数据库指标别名验证", "plan": plan})
    assert response.status_code == 201, response.text
    assert response.json()["plan"]["metric"] == "orders"
    assert response.json()["planText"].startswith("数据库订单指标 v9")
    saved = await session.scalar(select(QaCert).where(QaCert.code == response.json()["id"]))
    assert saved.metric_code == "order_cnt" and saved.metric_version == 9
    response = await qa.post(
        f"{B}/certs", json={"question": "数据库新指标验证", "plan": {**plan, "metric": "M01"}}
    )
    assert response.status_code == 201, response.text
    saved = await session.scalar(select(QaCert).where(QaCert.code == response.json()["id"]))
    assert saved.metric_code == "M01" and saved.metric_version is None
    metrics, _ = await catalog.directory(session)
    q = QaEvalQuestion(expect={"metric": "orders", "dims": []})

    def snap(name):
        return {"answer": {"table": {"columns": [{"title": name}]}}}

    assert job.judge(q, "answer", snap("有效订单数"), metrics)[0] == FailKind.wrong_metric
    assert job.judge(q, "answer", snap("数据库订单指标"), metrics) is None


async def test_catalog_history_is_frozen_and_missing_section_stays_empty(qa, session):
    base = await session.scalar(select(OntologyRelease).order_by(OntologyRelease.id.desc()).limit(1))
    for number, snapshot in [
        (
            98,
            {
                "metrics": {
                    "M01": {"id": "M01", "name": "历史指标", "version": "v2", "dimNames": ["历史维度"]}
                }
            },
        ),
        (99, {}),
    ]:
        session.add(
            OntologyRelease(
                ver=f"v2026.10.{number}",
                parent_id=base.id,
                published_by_id=base.published_by_id,
                published_at=base.published_at + timedelta(days=number),
                snapshot=snapshot,
                snapshot_sha256="0" * 64,
            )
        )
    await session.flush()
    historical = (await qa.get(f"{B}/plan-options", params={"ver": "v2026.10.98"})).json()
    assert historical["metrics"] == [{"code": "M01", "key": "M01", "name": "历史指标", "version": "v2"}]
    assert historical["dims"] == ["历史维度"]
    empty = (await qa.get(f"{B}/plan-options", params={"ver": "v2026.10.99"})).json()
    assert empty["metrics"] == [] and empty["dims"] == []
    certs = (await qa.get(f"{B}/certs", params={"ver": "v2026.10.99"})).json()
    assert "指标不可用" in certs["items"][0]["planText"]
    assert (await qa.get(f"{B}/plan-options", params={"ver": "missing"})).status_code == 404


async def test_eval_missing_database_metric_is_unavailable_without_llm_answer(qa, session):
    q = await session.scalar(select(QaEvalQuestion).where(QaEvalQuestion.code == "EQ-001"))
    q.expect = {"metric": "missing_metric", "dims": [], "time": "上周"}
    await session.flush()
    fake = FakeLLM("SELECT 1")
    app.dependency_overrides[get_llm] = lambda: fake
    response = await qa.post(f"{B}/eval/runs", json={"sessionId": "S0001", "group": "all"})
    assert response.status_code == 202
    code, group, _, user = qa.calls[0]
    run = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
    await job.execute(session, await session.connection(), fake, user, run, group, limit=1)
    detail = (await qa.get(f"{B}/eval/runs/{code}")).json()
    assert detail["total"] == 1 and detail["passed"] == 0 and not fake.prompts
    assert "期望指标不可用" in detail["fails"]["items"][0]["actual"]


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await users.flush()
    calls: list[tuple] = []

    async def enqueue(_session, *args):
        calls.append(args)
        return 5000 + len(calls)

    app.dependency_overrides[job.get_runner] = lambda: enqueue
    client.calls = calls  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.pop(job.get_runner, None)
    app.dependency_overrides.pop(get_llm, None)


async def test_run_eval_group(qa, session, monkeypatch):
    monkeypatch.setattr(job, "_WORKER_ID", "a" * 32)
    fake = FakeLLM('select name as "仓库", 0.9 as "出库及时率" from store order by store_no limit 2')
    app.dependency_overrides[get_llm] = lambda: fake
    before = (await qa.get(f"{B}/eval/runs")).json()["total"]
    r = await qa.post(f"{B}/eval/runs", json={"sessionId": "S0001", "group": "履约"})
    assert r.status_code == 202, r.text
    run = r.json()
    assert "workerId" not in run
    assert run["status"] == "待运行" and run["trigger"] == "手动运行" and run["scope"] == "履约相关 3 题"
    assert run["id"] == "ER-0026" and run["total"] == 3 and run["passed"] == 0
    assert run["version"] == await VersioningService().current_version(session)
    assert (await qa.get(f"{B}/eval/runs")).json()["total"] == before + 1
    code, group, llm, user = qa.calls[0]
    assert code == run["id"] and group == "履约" and llm is fake
    # 后台任务：测试里直接跑（同一测试事务、同一连接）
    x = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
    assert x.worker_id is None and x.queue_job_id == 5001
    assert x.execution_context["sessionId"] == "S0001"
    await job.execute(session, await session.connection(), fake, user, x, group)
    d = (await qa.get(f"{B}/eval/runs/{code}")).json()
    assert d["status"] == "已完成" and d["passed"] == 2 and d["failCount"] == 1
    f = d["fails"]["items"][0]
    assert f["qid"] == "EQ-018" and f["kind"] == "维度不匹配"
    qs = {
        q["id"]: q["last"]
        for q in (await qa.get(f"{B}/eval/questions", params={"group": "履约"})).json()["items"]
    }
    assert qs == {"EQ-017": "通过", "EQ-018": "失败", "EQ-019": "通过"}
    assert len(fake.prompts) == 3


async def test_eval_group_counts_and_validation_follow_database(qa, session):
    app.dependency_overrides[get_llm] = lambda: FakeLLM("unused")
    await session.execute(
        update(QaEvalQuestion).where(QaEvalQuestion.kind == "semplan").values(group_name="履约")
    )
    questions = (await qa.get(f"{B}/eval/questions", params={"size": 1})).json()
    assert questions["groupCounts"] == [{"group": "履约", "n": 40}]
    before = (await qa.get(f"{B}/eval/runs")).json()["total"]
    other_group = "退款"  # 仍是允许的分组，但数据库中已没有这个组的题。
    response = await qa.post(f"{B}/eval/runs", json={"sessionId": "S0001", "group": other_group})
    assert response.status_code == 422
    assert (await qa.get(f"{B}/eval/runs")).json()["total"] == before
    base = await session.scalar(select(OntologyRelease).order_by(OntologyRelease.id.desc()).limit(1))
    session.add(
        OntologyRelease(
            ver="v2026.10.99",
            parent_id=base.id,
            published_by_id=base.published_by_id,
            published_at=base.published_at + timedelta(days=1),
            snapshot=base.snapshot,
            snapshot_sha256=base.snapshot_sha256,
        )
    )
    await session.flush()
    response = await qa.post(f"{B}/eval/runs", json={"sessionId": "S0001", "group": "履约"})
    assert response.status_code == 202 and response.json()["total"] == 40
    assert response.json()["version"] == "v2026.10.99"
    summary = (await qa.get(f"{B}/summary")).json()
    assert summary["version"] == await VersioningService().current_version(session)
    assert summary["dataAsOf"] == ""


async def test_run_eval_judge_kinds(qa, session):
    app.dependency_overrides[get_llm] = lambda: FakeLLM("x")
    assert (await qa.post(f"{B}/eval/runs", json={"sessionId": "S0001", "group": "all"})).status_code == 202
    code, group, _, user = qa.calls[0]
    x = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
    assert group is None and x.scope == "全部 40 题"
    # 指标名对不上 → 指标选错；拒答 → 未召回指标（只跑前 2 题）
    await job.execute(
        session, await session.connection(), FakeLLM('select 1 as "门店数"'), user, x, group, limit=1
    )
    assert x.total == 1 and x.passed == 0
    d = (await qa.get(f"{B}/eval/runs/{code}")).json()
    assert d["fails"]["items"][0]["kind"] == "指标选错"
    assert (
        await qa.post(f"{B}/eval/runs", json={"sessionId": "S0001", "group": "不存在"})
    ).status_code == 422


async def test_eval_run_needs_llm(qa):
    def boom():
        from app.modules.qa.llm import QaLlmUnavailable

        raise QaLlmUnavailable()

    app.dependency_overrides[get_llm] = boom
    r = await qa.post(f"{B}/eval/runs", json={"sessionId": "S0001", "group": "all"})
    assert r.status_code == 503 and not qa.calls


async def _fb(qa, fid):
    return (await qa.get(f"{B}/feedbacks/{fid}")).json()


async def test_to_synonym_covered(qa):
    lst = (await qa.get(f"{B}/feedbacks", params={"status": "待处理", "size": 50})).json()
    assert lst["total"] == 15 and all("certBlock" in x for x in lst["items"])
    assert (await qa.get(f"{B}/summary")).json()["openFeedback"] == 15
    # FB-242「发货准时率」不在任何变更单 → 新建草稿变更单（目标 knowledge，synonyms 段 add 一行）
    r = await qa.post(f"{B}/feedbacks/FB-242:to-synonym", json={})
    assert r.status_code == 200, r.text
    b = r.json()
    cr = b["crId"]
    assert cr.startswith("CR-") and "FB-242" in b["affected"] and b["feedback"]["resolvedBy"] == cr
    d = (await qa.get(f"/api/ontology/v1/change-requests/{cr}")).json()
    assert d["status"] == "草稿" and d["targets"][0]["kind"] == "knowledge"
    items = (
        await qa.get(f"/api/ontology/v1/change-requests/{cr}/items", params={"section": "synonyms"})
    ).json()
    rows = items["items"] if isinstance(items, dict) else items
    assert [x["key"] for x in rows if x["section"] == "synonyms"] == ["发货准时率"]
    # 下拉用中文状态草稿能查到；同一反馈再转 → 409
    drafts = (await qa.get("/api/ontology/v1/change-requests", params={"status": "草稿"})).json()
    assert cr in str(drafts)
    assert (await qa.post(f"{B}/feedbacks/FB-242:to-synonym", json={})).status_code == 409


async def test_to_synonym_links_same_word(qa, session):
    from app.modules.qa.models import FbStatus, QaFeedback

    f = await session.scalar(select(QaFeedback).where(QaFeedback.code == "FB-242"))
    f2 = await session.scalar(select(QaFeedback).where(QaFeedback.code == "FB-233"))
    f.fix_text = f2.fix_text = "发货及时率"  # 两条同词待处理反馈
    await session.flush()
    d = await _fb(qa, "FB-242")
    assert d["coveredBy"] == "CR-0008" and d["coveredCrStatus"] == "已通过"
    r = await qa.post(f"{B}/feedbacks/FB-242:to-synonym", json={})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["crId"] == "CR-0008" and b["affected"] == ["FB-233", "FB-242"]
    assert b["feedback"]["status"] == "待发布生效" and b["feedback"]["resolvedBy"] == "CR-0008"
    assert f2.status == FbStatus.await_release
    assert (await qa.post(f"{B}/feedbacks/FB-242:to-synonym", json={})).status_code == 409


async def test_to_cert_new_variant_and_no_plan(qa):
    # FB-237「昨天出库及时率最差的仓库」：评测 / 高频里有计划 → 新建并认证
    d = await _fb(qa, "FB-237")
    if d["certBlock"]:
        pytest.skip("seed 里 FB-237 没有计划")
    assert d["plan"] and d["planText"]
    r = await qa.post(f"{B}/feedbacks/FB-237:to-cert")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["feedback"]["status"] == "已转认证问答" and b["feedback"]["resolvedBy"] == b["certId"]
    c = (await qa.get(f"{B}/certs/{b['certId']}")).json()
    assert c["source"] == "反馈纠错" and c["status"] in ("已认证", "待重新认证")
    if b["mode"] == "new":
        assert d["question"] in c["variants"] or d["question"] == c["question"]
    # 已处理不能再转
    assert (await qa.post(f"{B}/feedbacks/FB-237:to-cert")).status_code == 409
    # 没计划 → CERT_NO_PLAN
    blocked = [
        x
        for x in (await qa.get(f"{B}/feedbacks", params={"status": "待处理", "fixKind": "认证问答"})).json()[
            "items"
        ]
        if x["certBlock"]
    ]
    if blocked:
        r = await qa.post(f"{B}/feedbacks/{blocked[0]['id']}:to-cert")
        assert r.status_code == 422 and r.json()["code"] == "CERT_NO_PLAN"


async def test_to_cert_existing_adds_variant(qa):
    items = (await qa.get(f"{B}/feedbacks", params={"status": "待处理", "size": 50})).json()["items"]
    ex = [x for x in items if x.get("existingCert")]
    if not ex:
        pytest.skip("seed 里没有已有认证问答的待处理反馈")
    f = ex[0]
    r = (await qa.post(f"{B}/feedbacks/{f['id']}:to-cert")).json()
    assert r["mode"] == "variant" and r["certId"] == f["existingCert"]
    c = (await qa.get(f"{B}/certs/{r['certId']}")).json()
    assert f["question"] in c["variants"] or f["question"] == c["question"]


async def test_ignore_reopen(qa):
    r = await qa.post(f"{B}/feedbacks/FB-247:ignore")
    assert r.status_code == 200 and r.json()["status"] == "已忽略" and r.json()["handler"]
    assert (await qa.post(f"{B}/feedbacks/FB-247:ignore")).status_code == 409
    r = await qa.post(f"{B}/feedbacks/FB-247:reopen")
    assert r.status_code == 200 and r.json()["status"] == "待处理" and "handler" not in r.json()
    assert (await qa.post(f"{B}/feedbacks/FB-247:reopen")).status_code == 409
    assert (await qa.post(f"{B}/feedbacks/FB-248:reopen")).status_code == 409  # 待发布生效不能重新打开


async def test_writes_readonly_under_ver(qa):
    r = await qa.post(f"{B}/feedbacks/FB-247:ignore", params={"ver": "v2026.10.2"})
    assert r.status_code == 403 and r.json()["code"] == "READONLY"


async def test_run_code_serialized_across_transactions(engine):
    """两个事务同时取 ER- 号：后到的被咨询锁挡住，等前一个结束才读最大号，不会撞号。"""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core import clock
    from app.modules.qa.models import RunStatus
    from app.modules.qa.router import next_run_code

    async with engine.connect() as ca, engine.connect() as cb:
        await ca.begin()
        await cb.begin()
        sa_, sb = AsyncSession(bind=ca), AsyncSession(bind=cb)
        code_a = await next_run_code(sa_)
        sa_.add(
            QaEvalRun(
                code=code_a,
                run_at=clock.now(),
                trigger_label="手动运行",
                version_label="v",
                scope="s",
                total=0,
                passed=0,
                status=RunStatus.running,
                operator="t",
            )
        )
        await sa_.flush()
        tb = asyncio.create_task(next_run_code(sb))
        await asyncio.sleep(0.3)
        assert not tb.done()  # 被 A 的事务锁挡住
        await ca.commit()  # A 提交（释放锁）后 B 读到 A 的号
        try:
            code_b = await asyncio.wait_for(tb, 5)
            assert int(code_b[3:]) == int(code_a[3:]) + 1
        finally:
            await sb.rollback()
            async with engine.begin() as c:  # 清掉 A 提交的行
                await c.execute(QaEvalRun.__table__.delete().where(QaEvalRun.code == code_a))


async def test_recover_interrupted_eval_keeps_progress_and_skips_active_worker(engine, monkeypatch):
    """真实会话锁跨 checkpoint 保持；重启只恢复孤儿，停止 worker 后归属锁释放。"""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db import session as db_session
    from app.db.dialects import get_adapter
    from app.modules.qa.models import RunStatus

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "get_engine", lambda: engine)
    monkeypatch.setattr(db_session, "get_sessionmaker", lambda: factory)
    monkeypatch.setattr(db_session, "get_qa_readonly_engine", lambda: engine)
    codes = ["ER-recovery-stale", "ER-recovery-active", "ER-recovery-queued", "ER-recovery-done"]
    async with factory() as session:
        for code, status in zip(
            codes, [RunStatus.running, RunStatus.running, RunStatus.queued, RunStatus.done], strict=True
        ):
            session.add(
                QaEvalRun(
                    code=code,
                    trigger_label="手动运行",
                    scope="全部 3 题",
                    total=3,
                    passed=1,
                    status=status,
                    operator="test",
                )
            )
        await session.commit()

    started = asyncio.Event()

    async def executing(session, conn, llm, user, run, group, *, checkpoint):
        run.passed = 2
        await checkpoint()
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(job, "execute", executing)
    verifier_connection = await engine.connect()
    verifier = AsyncSession(bind=verifier_connection)
    active_task = None
    try:
        active_task = asyncio.create_task(job._background(codes[1], None, FakeLLM(""), None))
        await asyncio.wait_for(started.wait(), 5)
        assert await job.recover_interrupted_runs() == 1
        # 恢复先于原后台任务调度时，迟到的任务不能覆盖中断状态或重跑已写结果。
        await asyncio.wait_for(job._background(codes[0], None, FakeLLM(""), None), 5)
        async with factory() as session:
            runs = {
                run.code: run
                for run in (await session.scalars(select(QaEvalRun).where(QaEvalRun.code.in_(codes)))).all()
            }
            assert runs[codes[0]].status == RunStatus.failed
            assert runs[codes[0]].passed == 1 and runs[codes[0]].total == 3
            assert "运行中断" in runs[codes[0]].note
            assert runs[codes[1]].status == RunStatus.running and runs[codes[1]].passed == 2
            assert runs[codes[2]].status == RunStatus.queued and runs[codes[2]].note is None
            assert runs[codes[3]].status == RunStatus.done and runs[codes[3]].note is None
        active_task.cancel()
        await asyncio.gather(active_task, return_exceptions=True)
        # 固定另一条连接验证，避免原连接重入同名 session lock 导致假阳性。
        assert await get_adapter(verifier).try_xact_lock(verifier, f"qa.eval_run.{codes[1]}")
        await verifier.rollback()
        assert await job.recover_interrupted_runs() == 0
        async with factory() as session:
            active = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == codes[1]))
            assert active.status == RunStatus.failed and active.passed == 2
    finally:
        if active_task is not None:
            active_task.cancel()
            await asyncio.gather(active_task, return_exceptions=True)
        await verifier.close()
        await verifier_connection.close()
        async with engine.begin() as conn:
            await conn.execute(QaEvalRun.__table__.delete().where(QaEvalRun.code.in_(codes)))


async def test_live_worker_protects_committed_eval_before_background_dispatch(engine, monkeypatch):
    """活进程尚未调度的已提交 run，另一连接启动恢复不能误判中断。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import session as db_session
    from app.modules.qa.models import RunStatus

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_session, "get_engine", lambda: engine)
    monkeypatch.setattr(db_session, "get_sessionmaker", lambda: factory)
    code = "ER-dispatch-window"
    try:
        async with job.worker_lifespan():
            worker_id = job.current_worker_id()
            assert worker_id is not None and len(worker_id) == 32
            async with factory() as session:
                session.add(
                    QaEvalRun(
                        code=code,
                        worker_id=worker_id,
                        trigger_label="手动运行",
                        scope="全部 3 题",
                        total=3,
                        passed=0,
                        status=RunStatus.running,
                        operator="test",
                    )
                )
                await session.commit()
            # 全程没有调用 schedule_run，只有进程锁能保护提交至调度之间的窗口。
            assert await job.recover_interrupted_runs() == 0
            async with factory() as session:
                run = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
                assert run.status == RunStatus.running and run.note is None
        assert job.current_worker_id() is None
        assert await job.recover_interrupted_runs() == 1
        assert await job.recover_interrupted_runs() == 0
        async with factory() as session:
            run = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
            assert run.status == RunStatus.failed and "运行中断" in run.note
    finally:
        async with engine.begin() as connection:
            await connection.execute(QaEvalRun.__table__.delete().where(QaEvalRun.code == code))


async def test_reopen_after_to_cert_removes_variant(qa):
    items = (await qa.get(f"{B}/feedbacks", params={"status": "待处理", "size": 50})).json()["items"]
    for f in items:
        if f["certBlock"]:
            continue
        r = await qa.post(f"{B}/feedbacks/{f['id']}:to-cert")
        assert r.status_code == 200, r.text
        cid = r.json()["certId"]
        if f["question"] in (await qa.get(f"{B}/certs/{cid}")).json()["variants"]:
            break
    else:
        pytest.skip("seed 里没有能加同问法的待处理反馈")
    r = await qa.post(f"{B}/feedbacks/{f['id']}:reopen")
    assert r.status_code == 200 and r.json()["status"] == "待处理" and "resolvedBy" not in r.json()
    c = (await qa.get(f"{B}/certs/{cid}")).json()
    assert f["question"] not in c["variants"]  # 撤销了加上的同问法；认证问答本身保留 [推断]
    # 再转一次能重新加上
    assert (await qa.post(f"{B}/feedbacks/{f['id']}:to-cert")).json()["certId"] == cid
    assert f["question"] in (await qa.get(f"{B}/certs/{cid}")).json()["variants"]


async def test_feedback_list_bulk_fetch_query_count_and_detail_parity(qa, conn):
    from sqlalchemy import event

    statements = []

    def collect(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(conn.sync_connection, "before_cursor_execute", collect)
    try:
        small = await qa.get(f"{B}/feedbacks", params={"size": 1})
        assert small.status_code == 200, small.text
        small_queries = len(statements)
        statements.clear()
        large = await qa.get(f"{B}/feedbacks", params={"size": 15})
        assert large.status_code == 200, large.text
        assert len(large.json()["items"]) == 15
        assert len(statements) <= small_queries + 2
    finally:
        event.remove(conn.sync_connection, "before_cursor_execute", collect)
    for item in large.json()["items"][:3]:
        detail = await qa.get(f"{B}/feedbacks/{item['id']}")
        assert detail.status_code == 200 and detail.json() == item


async def test_catalog_uses_public_real_change_request_status(qa, session):
    from app.modules.static.models import ChangeRequest, CRStatus

    metrics, _ = await catalog.directory(session)
    assert metrics["refund"]["cr"] == "CR-0010"
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == "CR-0010"))
    cr.status = CRStatus.published
    await session.flush()
    metrics, _ = await catalog.directory(session)
    assert metrics["refund"]["cr"] is None
