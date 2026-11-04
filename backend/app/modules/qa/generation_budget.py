"""One logical generation budget per question, shared by every correction branch."""
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from app.core.errors import AppError

MAX_GENERATIONS = 3
P = ParamSpec("P")
T = TypeVar("T")


class GenerationBudget:
    def __init__(self) -> None:
        self.used = 0

    async def run(self, generate: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs) -> T:
        """Count before calling; propagate generation errors and cancellation unchanged."""
        if self.used >= MAX_GENERATIONS:
            raise AppError(
                "本次问数未能在3次生成内完成，请补充条件或简化问题后重新提问",
                code="QA_GENERATION_LIMIT",
                status_code=422,
                details={"limit": MAX_GENERATIONS, "used": self.used},
            )
        self.used += 1
        return await generate(*args, **kwargs)
