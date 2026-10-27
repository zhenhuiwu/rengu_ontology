"""No-network checks for the one-off smoke budget; all responses are explicit protocol fixtures."""

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from pydantic import SecretStr

from app.core.errors import AppError
from app.modules.qa.llm import QaLlmFailed
from scripts.model_smoke_budget import BASE_URL, MODEL, PROMPT_BYTES, SmokeBudget, deny_other_http

RUNTIME = SimpleNamespace(
    value=SimpleNamespace(model=MODEL, base_url=BASE_URL, enabled=True, timeout_sec=5),
    api_key=SecretStr("not-a-real-key"),
)
QUESTIONS = [f"question {index}" for index in range(10)]
PROMPT = [{"role": "user", "content": "hello"}]


async def expect_blocked(call):
    try:
        await call
    except (AppError, QaLlmFailed):
        return
    raise AssertionError("Budget boundary was not enforced")


async def success(request):
    return httpx.Response(
        200,
        json={
            "id": "fixture",
            "object": "chat.completion",
            "created": 1,
            "model": MODEL,
            "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "SELECT 1"}}
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        },
        request=request,
    )


async def check():
    with tempfile.TemporaryDirectory(prefix="model-smoke-budget-check-") as temporary:
        ledger = Path(temporary) / "budget.json"
        budget = SmokeBudget(RUNTIME, QUESTIONS, ledger, sender=success)
        assert budget.client.max_retries == 0
        try:
            await expect_blocked(budget.generate_sql(PROMPT))  # No authorized question context.
            budget.question_id.set(1)
            await expect_blocked(budget.generate_sql([{"role": "user", "content": "a" * PROMPT_BYTES}]))
            assert budget.summary()["requests"] == 0
            for index in range(1, 11):
                budget.question_id.set(index)
                assert await budget.generate_sql(PROMPT) == "SELECT 1"
                assert await budget.generate_sql(PROMPT) == "SELECT 1"
                await expect_blocked(budget.generate_sql(PROMPT))
            assert budget.summary()["requests"] == 20 and budget.summary()["reservedCny"] == "9.00"
        finally:
            await budget.close()
        restarted = SmokeBudget(RUNTIME, QUESTIONS, ledger, sender=success)
        try:
            restarted.question_id.set(1)
            await expect_blocked(restarted.generate_sql(PROMPT))
            assert restarted.summary()["requests"] == 20
        finally:
            await restarted.close()

        calls = []

        async def timeout(request):
            calls.append(1)
            raise httpx.ReadTimeout("explicit fixture timeout", request=request)

        failed = SmokeBudget(RUNTIME, QUESTIONS, Path(temporary) / "failed.json", sender=timeout)
        try:
            failed.question_id.set(1)
            await expect_blocked(failed.generate_sql(PROMPT))
            assert calls == [1]  # The SDK did not retry a failed POST.
            assert failed.summary()["reservedCny"] == "0.45"
            assert failed.summary()["records"][0]["state"] == "failed"
            assert failed.summary()["halted"]
            await expect_blocked(failed.generate_sql(PROMPT))
            assert calls == [1]  # Missing usage on timeout stops the run, not just this question.
        finally:
            await failed.close()

        async def missing_usage(request):
            response = await success(request)
            data = response.json()
            data.pop("usage")
            return httpx.Response(200, json=data, request=request)

        halted = SmokeBudget(RUNTIME, QUESTIONS, Path(temporary) / "halted.json", sender=missing_usage)
        try:
            halted.question_id.set(1)
            await expect_blocked(halted.generate_sql(PROMPT))
            assert halted.summary()["halted"] and halted.summary()["reservedCny"] == "0.45"
            await expect_blocked(halted.generate_sql(PROMPT))
            assert halted.summary()["requests"] == 1
        finally:
            await halted.close()

        streamed = SmokeBudget(RUNTIME, QUESTIONS, Path(temporary) / "stream.json", sender=success)
        app = FastAPI()

        @app.post("/sessions/test/turns:stream")
        async def stream(llm=Depends(streamed.dependency)):
            async def output():
                yield await llm.generate_sql(PROMPT)

            return StreamingResponse(output())

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post("/sessions/test/turns:stream", json={"question": QUESTIONS[0]})
                assert response.status_code == 200 and response.text == "SELECT 1"
            assert streamed.summary()["requests"] == 1
        finally:
            await streamed.close()

    deny_other_http()
    # No live socket is attempted: both standard transport entry points fail before network access.
    async with httpx.AsyncClient() as client:
        await expect_blocked(client.post("https://example.invalid/embedding"))
    print(
        "Budget checks passed: 20-call cap, per-question cap, durable restart, timeout debit, "
        "no SDK retry, missing usage halt, SSE, unrelated egress deny; real provider calls: 0"
    )


if __name__ == "__main__":
    asyncio.run(check())
