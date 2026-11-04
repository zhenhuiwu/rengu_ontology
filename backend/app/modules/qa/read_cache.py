"""Exact-query memoization for one answer, never a cross-request data cache.

The caller's key must include every query distinction (including scope, source,
projection and limit). Loaders return read-only scalars or lists of data values.
Reads sharing a database session remain sequential; this helper does not spawn
tasks or combine concurrent database queries.
"""

from collections.abc import Awaitable, Callable, Hashable
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
from typing import Any


@dataclass
class _Reads:
    values: dict[Hashable, Any] = field(default_factory=dict)
    active: bool = True


_reads: ContextVar[_Reads | None] = ContextVar("qa_answer_reads", default=None)


def read_scope[**P, T](func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Create a fresh answer cache; nested calls restore their parent's cache."""

    @wraps(func)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        reads = _Reads()
        token = _reads.set(reads)
        try:
            return await func(*args, **kwargs)
        finally:
            # Child tasks may inherit this context: invalidate their reference too.
            reads.active = False
            reads.values.clear()
            _reads.reset(token)

    return wrapped


def _copy[T](value: T) -> T:
    return deepcopy(value) if isinstance(value, list) else value


async def cached_read[T](key: Hashable, loader: Callable[[], Awaitable[T]]) -> T:
    """Memoize successful, exactly matching reads; list consumers get private copies."""
    reads = _reads.get()
    if reads is None or not reads.active:
        return await loader()
    if key not in reads.values:
        value = await loader()
        if not reads.active:
            return value
        reads.values[key] = _copy(value)
    return _copy(reads.values[key])
