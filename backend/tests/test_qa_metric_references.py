"""指标回滚前的公开QA软引用查询，不依赖指标名称或演示目录。"""

from sqlalchemy import select, update

from app.contracts.qa import has_metric_references
from app.core import clock
from app.modules.qa.models import (
    QaCert,
    QaEvalQuestion,
    QaFreqQuestion,
    QaPageCard,
    QaQueryLog,
    QaTemplateCard,
    QaTurn,
)
from app.seeds.loader import run_seeds


async def test_metric_references_cover_all_saved_code_and_plan_columns(users):
    await run_seeds(users, ["qa"])
    await users.flush()
    code = "M-reference-probe"
    assert not await has_metric_references(users, code)
    for model, plan in (
        (QaTurn, "sem_plan"),
        (QaPageCard, "sem_plan"),
        (QaTemplateCard, "sem_plan"),
        (QaCert, "sem_plan"),
        (QaEvalQuestion, "expect"),
        (QaFreqQuestion, "sem_plan"),
        (QaQueryLog, "sem_plan"),
    ):
        fields = [plan, "metric_code"] if hasattr(model, "metric_code") else [plan]
        for field in fields:
            savepoint = await users.begin_nested()
            if model is QaQueryLog:
                users.add(
                    QaQueryLog(
                        question="引用",
                        norm_question="引用",
                        asked_at=clock.now(),
                        kind="answer",
                        **{field: code if field == "metric_code" else {"metric": code}},
                    )
                )
                await users.flush()
            else:
                pk = await users.scalar(select(model.id).limit(1))
                assert pk is not None
                await users.execute(
                    update(model)
                    .where(model.id == pk)
                    .values(**{field: code if field == "metric_code" else {"metric": code}})
                )
            assert await has_metric_references(users, code), (model.__name__, field)
            await savepoint.rollback()
            assert not await has_metric_references(users, code)


async def test_metric_references_resolve_legacy_keys_without_metric_seed(session):
    session.add(QaEvalQuestion(code="EQ-reference", question="legacy引用", expect={"metric": "margin"}))
    await session.flush()
    assert await has_metric_references(session, "gross_margin")
    assert await has_metric_references(session, "margin")
    assert not await has_metric_references(session, "order_cnt")
