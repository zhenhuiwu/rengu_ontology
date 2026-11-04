"""Offline QA timing check: no database, HTTP or real model requests."""

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.qa import llm


async def check():
    now, records = [0.0], []
    secret = "PRIVATE_QUESTION_SQL_ENDPOINT_KEY"

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    class Fake:
        async def generate_sql(self, messages):
            now[0] += 0.025
            return secret

    class Broken:
        async def generate_sql(self, messages):
            now[0] += 0.01
            raise RuntimeError(secret)

    @llm.observe_answer
    async def broken_answer(session):
        return await llm.timed_generate_sql(Broken(), [{"content": secret}])

    @llm.observe_answer
    async def answer(session, fail=False, early=False):
        llm.timing_stage("understanding")
        now[0] += 0.02
        if early:
            return "early"
        for _ in range(2):
            llm.timing_stage("compiling")
            assert await llm.timed_generate_sql(Fake(), [{"content": secret}]) == secret
        if fail:
            raise ValueError(secret)
        now[0] += 0.03
        return "answer"

    handler = Capture()
    logger = llm._timing_log
    old_level, old_propagate = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        with patch.object(llm, "time", SimpleNamespace(monotonic=lambda: now[0])):
            session = SimpleNamespace(info={"ontology_id": 16})
            assert await answer(session) == "answer"
            total = records[-1]
            assert "totalMs=100.0" in total and "prepareMs=20.0" in total
            assert "modelCount=2 modelMs=50.0" in total
            assert sum("qa_model " in row for row in records) == 2
            assert any("attempt=2 durationMs=25.0" in row for row in records)
            try:
                await answer(session, fail=True)
            except ValueError:
                pass
            else:
                raise AssertionError("failure must propagate")
            assert "outcome=ValueError" in records[-1]
            assert "modelCount=2 modelMs=50.0" in records[-1]
            assert await answer(session, early=True) == "early"
            assert "modelCount=0 modelMs=0.0 prepareMs=20.0" in records[-1]
            try:
                await broken_answer(session)
            except RuntimeError:
                pass
            else:
                raise AssertionError("model failure must propagate")
            model_error = next(row for row in reversed(records) if row.startswith("qa_model "))
            assert "outcome=RuntimeError" in model_error
            assert "responseChars=0" in model_error
            assert "modelCount=1 modelMs=10.0" in records[-1]
            assert llm._timing.get() is None
            assert secret not in "\n".join(records)
            assert all("ontologyId=16" in row for row in records)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


if __name__ == "__main__":
    asyncio.run(check())
    print("QA timing offline check passed (counts, durations, early return, error, privacy).")
