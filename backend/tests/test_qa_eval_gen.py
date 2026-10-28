"""按本体自动出评测题（eval_gen.py）+ 每日任务登记 + 动作题判定 / 只跑本空间自动题。"""

import pytest
from sqlalchemy import select

from app.common.ontology import DEFAULT_ONTOLOGY_ID
from app.modules.qa import eval_gen, intent
from app.modules.qa.eval import job
from app.modules.qa.models import FailKind, QaEvalQuestion
from tests.qa_access_support import seed_qa_access


def test_question_builders_are_generic():
    ms = eval_gen.metric_questions(
        [
            {"key": "m1", "name": "指标甲", "dims": ["维度A", "维度B", "维度C"]},
            {"key": "m2", "name": " ", "dims": []},
        ]
    )
    assert [q for q, _, _ in ms] == ["指标甲", "按维度A看指标甲", "按维度B看指标甲"]
    assert ms[1][1] == {"metric": "m1", "dims": ["维度A"]} and ms[0][2] == "metric:m1"
    acts = eval_gen.action_questions(
        [{"id": "a1", "name": "动作乙", "target": "对象丙"}, {"id": "a2", "name": "动作丁"}]
    )
    assert [q for q, _, _ in acts] == ["给一个对象丙发起动作乙", "发起动作丁"]
    assert acts[0][1] == {"action": "a1", "actionName": "动作乙"}


async def test_generate_for_workspace_dedupes(users, session, monkeypatch):
    await seed_qa_access(users)
    await users.flush()

    async def fake_catalog(_s, user, request=None):
        assert user == "U"
        return [{"id": "act_x", "name": "动作X", "targetKind": "对象Y", "params": [], "policy": {}}]

    monkeypatch.setattr(intent, "action_catalog", fake_catalog)
    added = await eval_gen.generate(session, "U")
    assert added, "已发布指标应能出题"
    rows = list(await session.scalars(select(QaEvalQuestion).where(QaEvalQuestion.code.in_(added))))
    assert all(r.meta["auto"] and r.meta["ontologyId"] == DEFAULT_ONTOLOGY_ID for r in rows)
    assert all(r.kind.value == "semplan" and r.group_name is None for r in rows)
    assert any(r.expect.get("metric") for r in rows)
    act = next(r for r in rows if r.expect.get("action"))
    assert act.question == "给一个对象Y发起动作X" and act.meta["source"] == "action:act_x"
    assert await eval_gen.generate(session, "U") == []  # 第二次全部去重
    # 没有身份 → 只出指标题，不碰能力目录
    monkeypatch.setattr(intent, "action_catalog", None)
    assert await eval_gen._actions(session, None) == []


def test_worker_registers_daily_generation():
    from app.modules.qa.eval import worker

    assert eval_gen.TASK in worker.app.tasks
    periodic = worker.app.periodic_registry.periodic_tasks
    assert (eval_gen.TASK, eval_gen.TASK) in periodic
    assert periodic[(eval_gen.TASK, eval_gen.TASK)].cron == eval_gen.CRON


def _q(expect, meta=None):
    return QaEvalQuestion(code="EQ-T", question="q", kind="semplan", expect=expect, meta=meta)


def test_own_question_and_action_judge():
    assert job.own_question(_q({}), 1)
    assert job.own_question(_q({}, {"auto": True, "ontologyId": 1}), 1)
    assert not job.own_question(_q({}, {"auto": True, "ontologyId": 2}), 1)
    q = _q({"action": "act_x"})
    ok = {"answer": {"card": "action", "action": {"type": "act_x"}}}
    assert job.judge(q, "answer", ok, {}) is None
    wrong = {"answer": {"card": "action", "action": {"type": "act_y"}}}
    assert job.judge(q, "answer", wrong, {})[0] == FailKind.wrong_metric
    assert job.judge(q, "answer", {"answer": {"card": "clarify"}}, {})[0] == FailKind.metric_not_recalled
    assert job.judge(q, "denied", {"tag": "t"}, {})[0] == FailKind.metric_not_recalled


@pytest.mark.parametrize("plan,text", [({"action": "a", "actionName": "动作"}, "动作建议 · 动作")])
def test_plan_text_for_action(plan, text):
    from app.modules.qa import catalog

    assert catalog.plan_text(plan, {}) == text


def test_worker_registers_daily_eval_run():
    from app.modules.qa.eval import daily, worker

    periodic = worker.app.periodic_registry.periodic_tasks
    assert daily.TASK not in worker.app.tasks
    assert (daily.TASK, daily.TASK) not in periodic
    opted_in = worker.build_app(enable_daily=True)
    assert daily.TASK in opted_in.tasks
    assert opted_in.periodic_registry.periodic_tasks[(daily.TASK, daily.TASK)].cron == daily.CRON


async def test_daily_eval_run_is_created_and_queued_once_per_day(session):
    from datetime import timedelta
    from types import SimpleNamespace

    from app.core import clock
    from app.modules.qa.eval import daily
    from app.modules.qa.models import QaEvalRun, RunStatus

    session.add(QaEvalQuestion(code="EQ-daily", question="日评测题", kind="semplan", expect={"metric": "m"}))
    await session.flush()
    jobs = []

    async def fake_defer(_s, code):
        jobs.append(code)
        return 900 + len(jobs)

    who = SimpleNamespace(id="U-op", tenant_id=None)
    at = clock.now()
    code = await daily.schedule(session, who, at=at, defer=fake_defer)
    run = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
    assert jobs == [code] and run.queue_job_id == 901 and run.status == RunStatus.queued
    assert run.trigger_label == daily.TRIGGER and run.operator == "系统" and run.total >= 1
    assert run.execution_context["userId"] == "U-op" and run.execution_context["group"] is None
    assert await daily.schedule(session, who, at=at, defer=fake_defer) is None  # 还在排队 → 不叠加
    run.status = RunStatus.done
    await session.flush()
    assert await daily.schedule(session, who, at=at, defer=fake_defer) is None  # 今天已跑过
    nxt = await daily.schedule(session, who, at=at + timedelta(days=1), defer=fake_defer)
    assert nxt and nxt != code and len(jobs) == 2


async def test_daily_eval_skips_without_operator(session):
    from app.modules.qa.eval import daily

    async def boom(*_a):
        raise AssertionError("不应入队")

    assert await daily.schedule(session, None, defer=boom) is None
