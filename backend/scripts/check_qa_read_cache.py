"""Offline read-cache assertions; no database, HTTP or model requests."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.qa.read_cache import cached_read, read_scope


async def check():
    calls = []

    async def load(value):
        calls.append(value)
        return value

    @read_scope
    async def answer(label):
        key = ("scope", label, "table", "column", 31)
        original = [{"values": [label]}]
        first = await cached_read(key, lambda: load(original))
        first[0]["values"].append("consumer mutation")
        original[0]["values"].append("loader mutation")
        second = await cached_read(key, lambda: load("must not run"))
        assert second == [{"values": [label]}]
        assert await cached_read((*key[:-1], None), lambda: load("different limit")) == "different limit"
        number = 10**30
        assert await cached_read((label, "count"), lambda: load(number)) == number
        assert await cached_read((label, "count"), lambda: load(0)) == number
        await asyncio.sleep(0)
        assert await cached_read(key, lambda: load("must not run")) == second
        return second

    assert await answer("same") == await answer("same")
    assert len(calls) == 6  # Three distinct reads per answer, even with identical keys.
    await asyncio.gather(answer("parallel one"), answer("parallel two"))
    assert len(calls) == 12
    barrier = asyncio.Barrier(2)

    @read_scope
    async def concurrent(label):
        assert await cached_read("identical parallel key", lambda: load(label)) == label
        await barrier.wait()
        assert await cached_read("identical parallel key", lambda: load("wrong")) == label

    await asyncio.gather(concurrent("user one"), concurrent("user two"))

    @read_scope
    async def nested():
        assert await cached_read("nested", lambda: load("inner")) == "inner"

    @read_scope
    async def outer():
        assert await cached_read("nested", lambda: load("outer")) == "outer"
        await nested()
        assert await cached_read("nested", lambda: load("wrong")) == "outer"

    await outer()

    @read_scope
    async def broken():
        await cached_read("failure", lambda: load("inside failure"))
        raise ValueError("synthetic failure")

    try:
        await broken()
    except ValueError:
        pass
    else:
        raise AssertionError("scope must propagate exceptions")
    assert await cached_read("failure", lambda: load("outside failure")) == "outside failure"

    entered = asyncio.Event()

    @read_scope
    async def cancelled():
        await cached_read("cancel", lambda: load("inside cancel"))
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(cancelled())
    await entered.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("scope must propagate cancellation")
    assert await cached_read("cancel", lambda: load("outside cancel")) == "outside cancel"

    @read_scope
    async def cancel_nested():
        await cached_read("cancel", lambda: load("outer survives"))
        try:
            async with asyncio.timeout(0):
                await cancelled()
        except TimeoutError:
            pass
        assert await cached_read("cancel", lambda: load("wrong")) == "outer survives"

    await cancel_nested()
    await cached_read("no scope", lambda: load("outside one"))
    assert await cached_read("no scope", lambda: load("outside two")) == "outside two"


if __name__ == "__main__":
    asyncio.run(check())
    print(
        "QA read-cache offline check passed: exact keys, copy safety, nested/concurrent scope, errors/cancel."
    )
