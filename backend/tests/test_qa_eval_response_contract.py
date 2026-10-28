"""Explicit scoring contracts and counterexamples, without model calls."""

from types import SimpleNamespace

import pytest

from app.modules.qa.eval import job
from app.modules.qa.eval.run import has_draft_notice

METRICS = {"m": {"name": "指标甲", "code": "M"}}
REASON = "来源甲尚未接入"


def question(expect):
    return SimpleNamespace(expect=expect)


def refusal():
    return {"code": "NO_DATA", "bold": REASON, "tag": "无法回答"}


def clarify():
    return {
        "answer": {
            "card": "clarify",
            "text": "请选择目标对象",
            "clarify": {
                "term": "目标对象",
                "chosen": None,
                "options": [{"value": "o1", "label": "对象甲"}, {"value": "o2", "label": "对象乙"}],
            },
        }
    }


def no_data_expect(target):
    return {**target, "refuse": {"code": "NO_DATA", "reason": REASON}}


def clarify_expect(target):
    return {**target, "allowClarify": {"term": "目标对象", "options": ["对象甲", "对象乙"]}}


@pytest.mark.parametrize("target", [{"metric": "m"}, {"action": "A"}])
def test_only_explicit_matching_no_data_is_accepted(target):
    q = question(no_data_expect(target))
    assert job.judge(q, "denied", refusal(), METRICS, sql=None) is None
    assert job.judge(q, "denied", refusal(), METRICS) is not None  # unknown SQL evidence
    assert job.judge(q, "denied", refusal(), METRICS, sql="select 1") is not None
    assert job.judge(question(target), "denied", refusal(), METRICS, sql=None) is not None


@pytest.mark.parametrize(
    "changes",
    [
        {"code": "NO_PERMISSION"},
        {"code": "OUT_OF_SCOPE"},
        {"code": "NOT_SELECT"},
        {"bold": "来源乙尚未接入"},
        {"bold": ""},
        {"answer": {"card": "action", "action": {"type": "A"}}},
        {"table": {"columns": []}},
        {"table": {}},
        {"action": {}},
        {"action": {"type": "A"}},
        {"answer": {}},
    ],
)
def test_wrong_refusal_code_reason_or_executed_payload_never_passes(changes):
    snap = {**refusal(), **changes}
    assert job.judge(question(no_data_expect({"metric": "m"})), "denied", snap, METRICS, sql=None) is not None


def test_unavailable_metric_cannot_be_rescued_by_refusal_expectation():
    assert (
        job.judge(question(no_data_expect({"metric": "missing"})), "denied", refusal(), METRICS, sql=None)
        is not None
    )


@pytest.mark.parametrize(
    "rubric",
    [
        {"code": "NO_PERMISSION", "reason": REASON},
        {"code": "NO_DATA"},
        {"code": "NO_DATA", "reason": ""},
        {"code": "NO_DATA", "reason": REASON, "anything": True},
        None,
    ],
)
def test_invalid_refusal_rubric_fails_closed(rubric):
    assert (
        job.judge(question({"metric": "m", "refuse": rubric}), "denied", refusal(), METRICS, sql=None)
        is not None
    )


@pytest.mark.parametrize("target", [{"metric": "m"}, {"action": "A"}])
def test_only_reviewed_clarification_term_and_candidate_set_pass(target):
    q = question(clarify_expect(target))
    assert job.judge(q, "answer", clarify(), METRICS, sql=None) is None
    assert job.judge(q, "answer", clarify(), METRICS) is not None
    assert job.judge(q, "answer", clarify(), METRICS, sql="select 1") is not None
    assert job.judge(question(target), "answer", clarify(), METRICS, sql=None) is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("term", "时间范围"),
        ("chosen", "o1"),
        ("options", []),
        ("options", [{"value": "o1", "label": "对象丙"}, {"value": "o2", "label": "对象乙"}]),
        ("options", [{"value": "o1", "label": "对象甲"}, {"value": "o2", "label": "对象甲"}]),
        ("options", [{"value": "o1", "label": "对象甲"}, {"value": "o1", "label": "对象乙"}]),
        ("options", [{"label": "对象甲"}, {"value": "o2", "label": "对象乙"}]),
    ],
)
def test_unrelated_or_malformed_clarification_never_passes(field, value):
    snap = clarify()
    snap["answer"]["clarify"][field] = value
    assert job.judge(question(clarify_expect({"action": "A"})), "answer", snap, METRICS, sql=None) is not None


@pytest.mark.parametrize(
    "key,value",
    [
        ("table", {"items": [{"c0": 1}]}),
        ("action", {"type": "A"}),
        ("table", {}),
        ("action", {}),
        ("text", ""),
    ],
)
def test_clarification_with_result_action_or_empty_question_fails(key, value):
    snap = clarify()
    snap["answer"][key] = value
    assert job.judge(question(clarify_expect({"action": "A"})), "answer", snap, METRICS, sql=None) is not None


def test_allow_clarify_does_not_rescue_wrong_normal_answer_or_action():
    action_q = question(clarify_expect({"action": "A"}))
    assert (
        job.judge(action_q, "answer", {"answer": {"card": "action", "action": {"type": "A"}}}, METRICS)
        is None
    )
    assert (
        job.judge(action_q, "answer", {"answer": {"card": "action", "action": {"type": "B"}}}, METRICS)
        is not None
    )
    metric_q = question(clarify_expect({"metric": "m"}))
    assert (
        job.judge(metric_q, "answer", {"answer": {"table": {"columns": [{"title": "指标乙"}]}}}, METRICS)
        is not None
    )


def test_legacy_expectations_preserve_existing_call_signature():
    assert (
        job.judge(
            question({"action": "A"}), "answer", {"answer": {"card": "action", "action": {"type": "A"}}}, {}
        )
        is None
    )
    assert (
        job.judge(
            question({"metric": "m"}),
            "answer",
            {"answer": {"table": {"columns": [{"title": "指标甲"}]}}},
            METRICS,
        )
        is None
    )


@pytest.mark.parametrize("text", ["草稿口径。结果为12。", "结果为12；本结果采用草稿口径。", "草稿口径"])
def test_explicit_affirmative_visible_draft_notice(text):
    assert has_draft_notice("answer", {"answer": {"card": "table", "text": text}}, "select 12")


@pytest.mark.parametrize(
    "snap",
    [
        {"answer": {"text": "不是草稿口径。"}},
        {"answer": {"text": "不是，草稿口径。"}},
        {"answer": {"text": "无需草稿口径提示。"}},
        {"answer": {"text": "已发布口径。", "table": {"title": "草稿口径"}}},
        {"answer": {"text": "结果12。"}, "question": "草稿口径"},
        {"answer": {"card": "clarify", "text": "草稿口径"}},
    ],
)
def test_negation_or_hidden_mention_is_not_a_notice(snap):
    assert not has_draft_notice("answer", snap, "select 12 /*草稿口径*/")


def test_visible_basis_notice_and_execution_evidence():
    snap = {"answer": {"text": "结果12。", "basis": {"caliber": "草稿口径"}}}
    assert has_draft_notice("answer", snap, "select 12")
    assert not has_draft_notice("denied", snap, None)
    assert not has_draft_notice("answer", snap, None)
    assert not has_draft_notice("answer", snap, "   ")


@pytest.mark.parametrize("kind", ["denied", "unavailable", "error"])
def test_other_response_kinds_do_not_satisfy_clarification(kind):
    assert (
        job.judge(question(clarify_expect({"action": "A"})), kind, clarify(), METRICS, sql=None) is not None
    )


@pytest.mark.parametrize(
    "rubric",
    [
        None,
        {"term": "目标对象"},
        {"term": "目标对象", "options": ["对象甲", "对象甲"]},
        {"term": "目标对象", "options": ["对象甲", "对象乙"], "allowAll": True},
    ],
)
def test_invalid_clarification_rubric_fails_closed(rubric):
    assert (
        job.judge(question({"action": "A", "allowClarify": rubric}), "answer", clarify(), METRICS, sql=None)
        is not None
    )


def test_no_data_rubric_cannot_accept_normal_answer_or_contradictory_rubrics():
    q = question(no_data_expect({"metric": "m"}))
    snap = {"answer": {"table": {"columns": [{"title": "指标甲"}]}}}
    assert job.judge(q, "answer", snap, METRICS, sql=None) is not None
    exp = no_data_expect({"action": "A"}) | clarify_expect({"action": "A"})
    assert job.judge(question(exp), "denied", refusal(), METRICS, sql=None) is not None


async def test_execute_uses_returned_sql_for_explicit_refusal(session, monkeypatch):
    from sqlalchemy import select

    from app.modules.qa import chain
    from app.modules.qa.models import EvalLast, QaEvalQuestion, QaEvalRun, RunStatus

    questions = [
        QaEvalQuestion(
            code=f"EQ-contract-{i}", question=f"contract-{i}", expect=no_data_expect({"action": "A"})
        )
        for i in (1, 2)
    ]
    run = QaEvalRun(
        code="ER-contract",
        trigger_label="测试",
        scope="合同",
        total=0,
        passed=0,
        status=RunStatus.running,
        operator="test",
    )
    session.add_all([*questions, run])
    await session.flush()
    monkeypatch.setattr(
        job,
        "questions_query",
        lambda _group: (
            select(QaEvalQuestion)
            .where(QaEvalQuestion.code.in_([q.code for q in questions]))
            .order_by(QaEvalQuestion.code)
        ),
    )

    async def answer(_session, _conn, _llm, _user, question, **_kwargs):
        return "denied", refusal(), 0, 0, None if question.endswith("1") else "select 1"

    monkeypatch.setattr(chain, "answer", answer)
    await job.execute(session, await session.connection(), None, None, run, None)
    assert run.total == 2 and run.passed == 1
    assert [q.last_result for q in questions] == [EvalLast["pass"], EvalLast.fail]


async def test_fixed_eval_requires_both_gold_match_and_visible_notice(engine, monkeypatch):
    import asyncio

    from app.common import auth
    from app.modules.qa import chain
    from app.modules.qa.eval import run

    async def eval_user(_session):
        return "test"

    async def resolve(_session, _user):
        return None

    answer = {"card": "table", "text": "草稿口径", "table": {"items": [{"c0": 12}]}}

    async def fake_answer(*_args, **_kwargs):
        return "answer", {"answer": answer}, 1, 0, "select 12"

    monkeypatch.setattr(run, "eval_user_code", eval_user)
    monkeypatch.setattr(auth, "resolve_user", resolve)
    monkeypatch.setattr(chain, "answer", fake_answer)
    question = {
        "code": "contract",
        "question": "测试",
        "expectedType": "answer",
        "goldSql": "select 12",
        "checks": ["result", "draftNotice"],
    }
    assert (await run.one(engine, None, question, asyncio.Semaphore(1)))["ok"]
    answer["text"] = "不是，草稿口径"
    assert not (await run.one(engine, None, question, asyncio.Semaphore(1)))["ok"]
    answer["text"] = "草稿口径"
    answer["table"]["items"][0]["c0"] = 13
    assert not (await run.one(engine, None, question, asyncio.Semaphore(1)))["ok"]


@pytest.mark.parametrize(
    "text",
    [
        "草稿口径，但实际使用已发布口径",
        "本结果采用草稿口径。实际使用已发布口径。",
    ],
)
def test_draft_notice_rejects_explicit_published_caliber_contradiction(text):
    assert not has_draft_notice("answer", {"answer": {"text": text}}, "select 12")


def test_draft_notice_rejects_conflicting_visible_basis():
    snap = {"answer": {"text": "草稿口径", "basis": {"caliber": "已发布口径"}}}
    assert not has_draft_notice("answer", snap, "select 12")


@pytest.mark.parametrize(
    "expected,kind,snap,sql,checks,ok",
    [
        ("refuse", "denied", {"code": "NO_DATA"}, None, [], True),
        ("refuse", "denied", {"code": "NO_DATA"}, "select 1", [], False),
        ("refuse", "denied", {"code": "NO_DATA", "answer": {}}, None, [], False),
        ("refuse", "denied", {"code": "NO_DATA", "action": {}}, None, [], False),
        ("clarify", "answer", {"answer": {"clarify": {}}}, None, [], True),
        ("clarify", "answer", {"answer": {"clarify": {}, "table": {}}}, None, [], False),
        ("answer", "answer", {"answer": {"clarify": {}}}, None, ["orClarify"], True),
        ("answer", "answer", {"answer": {"clarify": {}, "action": {}}}, None, ["orClarify"], False),
        ("answer", "answer", {"answer": {"clarify": {}}}, "select 1", ["orClarify"], False),
    ],
)
async def test_fixed_eval_nonanswer_has_no_execution_or_payload(
    engine, monkeypatch, expected, kind, snap, sql, checks, ok
):
    import asyncio

    from app.common import auth
    from app.modules.qa import chain
    from app.modules.qa.eval import run

    async def eval_user(_session):
        return "test"

    async def resolve(_session, _user):
        return None

    async def answer(*_args, **_kwargs):
        return kind, snap, 0, 0, sql

    monkeypatch.setattr(run, "eval_user_code", eval_user)
    monkeypatch.setattr(auth, "resolve_user", resolve)
    monkeypatch.setattr(chain, "answer", answer)
    q = {
        "code": "contract",
        "question": "test",
        "expectedType": expected,
        "refuseCode": "NO_DATA",
        "checks": checks,
    }
    assert (await run.one(engine, None, q, asyncio.Semaphore(1)))["ok"] is ok


@pytest.mark.parametrize(
    "gold,kind,snap,sql,ok",
    [
        ("select 1 where false", "denied", {"code": "NO_DATA"}, None, False),
        ("select 1 where false", "empty", {"tag": "无数据"}, None, True),
        ("select 1 where false", "empty", {"tag": "无数据"}, "select 1 where false", True),
        ("select 1 where false", "answer", {"answer": {"table": {"items": [{"c0": 0}]}}}, "select 0", False),
        ("select null", "empty", {"tag": "无数据"}, None, True),
        ("select null", "answer", {"answer": {"table": {"items": [{"c0": 0}]}}}, "select 0", True),
        (
            "select null union all select null",
            "answer",
            {"answer": {"table": {"items": [{"c0": 0}]}}},
            "select 0",
            False,
        ),
        (
            "select null union all select null",
            "answer",
            {"answer": {"table": {"items": [{"c0": 0}, {"c0": 0}]}}},
            "select 0 union all select 0",
            True,
        ),
        (
            "select 12",
            "answer",
            {"answer": {"table": {"items": [{"c0": 12, "c1": "名称"}]}}},
            "select 12",
            True,
        ),
    ],
)
async def test_fixed_eval_empty_and_null_results(engine, monkeypatch, gold, kind, snap, sql, ok):
    import asyncio

    from app.common import auth
    from app.modules.qa import chain
    from app.modules.qa.eval import run

    async def eval_user(_session):
        return "test"

    async def resolve(_session, _user):
        return None

    async def answer(*_args, **_kwargs):
        return kind, snap, 0, 0, sql

    monkeypatch.setattr(run, "eval_user_code", eval_user)
    monkeypatch.setattr(auth, "resolve_user", resolve)
    monkeypatch.setattr(chain, "answer", answer)
    q = {"code": "contract", "question": "test", "expectedType": "answer", "goldSql": gold}
    assert (await run.one(engine, None, q, asyncio.Semaphore(1)))["ok"] is ok
