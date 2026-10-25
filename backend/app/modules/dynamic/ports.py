"""对外依赖的接口与桩（STUB）。

动态只通过这些接口调别的模块：
- ActionSubmitter（app.contracts.actions，Palantir 写回适配器）：审批通过 / 重新提交后调 submit；
- EventEmitter（integration）：发 ActionExecuted / ActionOutcomeUnknown；
- ReconcileScheduler（integration）：人工对账「仍无法确认」时 reopen_reconcile；
- SubmissionChecker（动作类型的提交条件，Palantir 模块未合并）：:start-action / :stage / :confirm 重新校验。

每个都是 FastAPI 依赖（get_*），合并后把 get_* 换成真实实现即可；测试用 app.dependency_overrides 注入。
桩的行为：submit 一律成功（回执号 STUB-<幂等键>）；emit / reopen 只记在内存列表里；提交条件一律通过。
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol, runtime_checkable

from fastapi import Depends

from app.contracts.actions import (
    ActionSubmitter,
    EventEmitter,
    ReconcileScheduler,
    SubmitResult,
    SubmitStatus,
)


@runtime_checkable
class SubmissionChecker(Protocol):
    """动作类型提交条件校验：返回不通过的项 [{name, fail}]，空列表 = 通过。"""

    async def check(self, action_type_code: str, context: dict[str, Any]) -> list[dict[str, str]]: ...


class StubSubmitter:
    """STUB：一律同步成功。"""

    async def submit(self, action_run_id: str, idempotency_key: str) -> SubmitResult:
        return SubmitResult(status=SubmitStatus.SUCCESS, receipt_id=f"STUB-{idempotency_key}", code="200")


@dataclass
class RecordingEmitter:
    """STUB：只记录，不投递。"""

    events: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)

    async def emit(self, event_id: str, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((event_id, event_type, payload))


@dataclass
class RecordingScheduler:
    """STUB：只记录 reopen 的动作记录号。"""

    reopened: list[str] = field(default_factory=list)

    async def reopen_reconcile(self, action_run_id: str) -> None:
        self.reopened.append(action_run_id)


class StubSubmissionChecker:
    """STUB：提交条件一律通过（动作类型的 submitConds 在 Palantir 模块，未合并）。"""

    async def check(self, action_type_code: str, context: dict[str, Any]) -> list[dict[str, str]]:
        return []


_submitter = StubSubmitter()
_emitter = RecordingEmitter()
_scheduler = RecordingScheduler()
_checker = StubSubmissionChecker()


def get_submitter() -> ActionSubmitter:
    return _submitter


def get_emitter() -> EventEmitter:
    return _emitter


def get_scheduler() -> ReconcileScheduler:
    return _scheduler


def get_checker() -> SubmissionChecker:
    return _checker


@dataclass
class Ports:
    submitter: ActionSubmitter
    emitter: EventEmitter
    scheduler: ReconcileScheduler
    checker: SubmissionChecker


def get_ports(
    submitter: Annotated[ActionSubmitter, Depends(get_submitter)],
    emitter: Annotated[EventEmitter, Depends(get_emitter)],
    scheduler: Annotated[ReconcileScheduler, Depends(get_scheduler)],
    checker: Annotated[SubmissionChecker, Depends(get_checker)],
) -> Ports:
    return Ports(submitter, emitter, scheduler, checker)


PortsDep = Annotated[Ports, Depends(get_ports)]
