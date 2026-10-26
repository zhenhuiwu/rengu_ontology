"""动作执行适配器；未配置的运行通道明确失败，测试替身只能显式注入。

动态只通过这些接口调别的模块：
- ActionSubmitter（app.contracts.actions，Palantir 写回适配器）：审批通过 / 重新提交后调 submit；
- EventEmitter（integration）：发 ActionExecuted / ActionOutcomeUnknown；
- ReconcileScheduler（integration）：人工对账「仍无法确认」时 reopen_reconcile；
- SubmissionChecker（动作类型的提交条件，Palantir 模块未合并）：:start-action / :stage / :confirm 重新校验。

每个都是 FastAPI 依赖（get_*），合并后把 get_* 换成真实实现即可；测试用 app.dependency_overrides 注入。
本文件保留测试替身，默认服务依赖不会使用它们。
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Protocol, runtime_checkable

from fastapi import Depends

from app.common.auth import CurrentUserDep
from app.contracts.actions import (
    ActionSubmitter,
    EventEmitter,
    ReconcileOutcome,
    ReconcileScheduler,
    SubmitResult,
    SubmitStatus,
)
from app.core.errors import AppError
from app.db.session import SessionDep


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
    resolved: list[tuple[str, ReconcileOutcome, str]] = field(default_factory=list)

    async def reopen_reconcile(self, action_run_id: str) -> None:
        self.reopened.append(action_run_id)

    async def resolve_reconcile(self, action_run_id: str, outcome: ReconcileOutcome, reconciler: str) -> None:
        self.resolved.append((action_run_id, outcome, reconciler))


class StubSubmissionChecker:
    """STUB：提交条件一律通过（动作类型的 submitConds 在 Palantir 模块，未合并）。"""

    async def check(self, action_type_code: str, context: dict[str, Any]) -> list[dict[str, str]]:
        return []


class UnconfiguredAdapters:
    async def submit(self, action_run_id: str, idempotency_key: str) -> SubmitResult:
        raise AppError("动作执行通道尚未配置", code="ACTION_EXECUTOR_UNAVAILABLE", status_code=503)

    async def emit(self, event_id: str, event_type: str, payload: dict[str, object]) -> None:
        raise AppError("事件投递通道尚未配置", code="EVENT_DELIVERY_UNAVAILABLE", status_code=503)

    async def reopen_reconcile(self, action_run_id: str) -> None:
        raise AppError("自动对账任务尚未配置", code="RECONCILE_UNAVAILABLE", status_code=503)

    async def resolve_reconcile(self, action_run_id: str, outcome: ReconcileOutcome, reconciler: str) -> None:
        raise AppError("对账任务尚未配置", code="RECONCILE_UNAVAILABLE", status_code=503)

    async def check(self, action_type_code: str, context: dict[str, Any]) -> list[dict[str, str]]:
        raise AppError("动作条件执行器尚未配置", code="ACTION_CHECKER_UNAVAILABLE", status_code=503)


_unconfigured = UnconfiguredAdapters()


def get_submitter(session: SessionDep, user: CurrentUserDep) -> ActionSubmitter:
    from app.modules.integration.execution import DatabaseActionSubmitter

    return DatabaseActionSubmitter(session, user)


def get_emitter(session: SessionDep) -> EventEmitter:
    from app.modules.integration.emitter import DatabaseEventEmitter

    return DatabaseEventEmitter(session)


def get_scheduler(session: SessionDep) -> ReconcileScheduler:
    from app.modules.integration.reconcile import DatabaseReconcileScheduler

    return DatabaseReconcileScheduler(session)


def get_checker(session: SessionDep, user: CurrentUserDep) -> SubmissionChecker:
    from app.modules.action.checker import DatabaseSubmissionChecker

    return DatabaseSubmissionChecker(session, user)


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
