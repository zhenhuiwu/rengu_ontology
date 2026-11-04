"""Run from backend: .venv/Scripts/python.exe -m scripts.check_qa_generation_budget.

Pure in-memory async calls; no DB, HTTP, model, worker or pytest.
"""
import asyncio

from sqlalchemy.exc import DBAPIError

from app.common.sql_guard import SqlRejected
from app.core.errors import AppError
from app.modules.qa.generation_budget import GenerationBudget


async def check():
    calls = []

    async def generate(reason, *, result):
        calls.append(reason)
        await asyncio.sleep(0)
        return result

    budget = GenerationBudget()
    for reason in ("initial", "schema correction", "database correction"):
        assert await budget.run(generate, reason, result=reason) == reason
    try:
        await budget.run(generate, "different correction branch", result="must not run")
    except AppError as error:
        assert error.code == "QA_GENERATION_LIMIT" and error.status_code == 422
        assert error.details == {"limit": 3, "used": 3}
        assert error.message
    else:
        raise AssertionError("fourth logical generation was allowed")
    assert calls == ["initial", "schema correction", "database correction"]

    other_question = GenerationBudget()
    assert await other_question.run(generate, "another question", result=7) == 7
    assert other_question.used == 1 and budget.used == 3

    for original in (SqlRejected(), DBAPIError(None, None, RuntimeError("synthetic DB error"))):
        failed = GenerationBudget()

        async def reject(error=original):
            raise error

        try:
            await failed.run(reject)
        except Exception as observed:
            assert observed is original, "SQL/DB errors must propagate unchanged"
        else:
            raise AssertionError("generation error was swallowed")
        assert failed.used == 1, "failed generation still consumes its logical attempt"

    cancelled = GenerationBudget()
    entered, waiting = asyncio.Event(), asyncio.Event()
    cancellation = []

    async def wait():
        entered.set()
        try:
            await waiting.wait()
        except asyncio.CancelledError as error:
            cancellation.append(error)
            raise

    task = asyncio.create_task(cancelled.run(wait))
    await entered.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError as error:
        assert error is cancellation[0], "cancellation must propagate unchanged"
    else:
        raise AssertionError("cancellation was swallowed")
    assert cancelled.used == 1
    print(
        "QA generation budget passed: shared three-attempt limit, no fourth call, "
        "unchanged SQL/DB errors and cancellation, independent questions."
    )


if __name__ == "__main__":
    asyncio.run(check())
