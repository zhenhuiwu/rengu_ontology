"""契约 2：动态本体（动作中心 / 自动化）↔ 动作执行与对账（写回）。

规则（团队约定）：
1. 幂等键在「暂存」（生成待确认的 ActionRun）时生成并落库（列名 `idem_key`），之后不再变化。
   格式跟前端 mock：`act-<动作记录号去掉 "ACT-" 后小写>`，如 ACT-20261025-3834 → act-20261025-3834
   （`idempotency_key_for()`）；
2. 提交：`ActionSubmitter.submit(action_run_id, idempotency_key)` → SubmitResult
   - 适配器内部可以用**同一个键**重试，最多 ext_system.maxRetry 次（calls[] 记每次尝试）；
   - SUCCESS：外部系统确认成功，带回执号 receipt_id；
   - FAILED：确定失败（可以重新提交）；
   - UNKNOWN：超时等结果未知 —— 适配器重试用完后**不再自动重试**，等待对账；
3. 对账结束时执行方回调 `ReconciliationListener.on_reconciled(action_run_id, result)`；
   结果只有两种：succeeded（外部已成功）/ not_executed（外部未执行）。
   not_executed 的那条记录**不能**直接重新提交（前端对账结论只读），要重做就从页面再发起；
4. 重新提交（仅 FAILED）= 新建一个 ActionRun + 新幂等键 `<原键去掉 -rN>-r<N+1>`（`new_resubmit_key`），
   旧记录保留不改，和前端 mock 的 `-r1/-r2` 后缀一致；
5. 对账查询 `ReconcileQuery.query_by_key(idempotency_key)` → 查到·成功 / 查到·未执行 / 查无（ExtStatus），
   「查无」也算一次。定时对账是周期任务 action.reconcile（*/15，归 integration），扫 reconcile_task.next_at
   到期的记录，每轮最多 3 次，3 次仍查无 → 转人工（state=manual、next_at=null）。
   外部系统不支持幂等查询（ext_system.idempotent=false，如短信、企业微信）时**从不调用** query_by_key：
   对账任务建好即转人工；
6. 同步提交后的分工：
   - SUCCESS → dynamic 在同一事务里提交本体变更（ActionRun 状态、对象属性），并发 ActionExecuted 事件；
   - UNKNOWN → dynamic 生成 eventId，经 integration 的 `EventEmitter.emit` 发 ActionOutcomeUnknown，
     integration 建 reconcile_task 进入定时对账；
7. 人工对账的选项：外部已成功 / 外部未执行 / **仍无法确认**。「仍无法确认」是人工选择（不是 query_by_key
   的返回值），dynamic 调 `ReconcileScheduler.reopen_reconcile(action_run_id)`：integration 把该任务
   round+1、tries=0、next_at=clock.now()（下一次 */15 扫描就会处理）；
   idempotent=false 的系统 reopen 后**仍停在人工**（不入队、不查询）；
8. seed：结果未知的对账任务共 74 条（ACTIONS 20 + RUNS 54），状态是自动轮次已用完：
   tries=3、转人工、next_at=null（不会被定时任务扫到，等人工 reopen）。

字段与 ui-vue/docs/backend/action-event-api.md §1.1 对齐：result=成功|失败|结果未知 → SubmitStatus 的 label；
ext_ref → receipt_id；code、retries、elapsed_ms、calls[] 为可选补充。
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class SubmitStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    @property
    def label(self) -> str:
        return {"SUCCESS": "成功", "FAILED": "失败", "UNKNOWN": "结果未知"}[self.value]


@dataclass(frozen=True)
class CallAttempt:
    attempt: int
    http: int | None  # 超时无响应为 None
    ms: int


@dataclass(frozen=True)
class SubmitResult:
    status: SubmitStatus
    receipt_id: str | None = None  # 外部回执号 ext_ref（本体内部动作可空）
    reason: str | None = None  # 失败 / 结果未知的原因，中文，可直接展示
    code: str | None = None  # 外部返回码
    retries: int = 0
    elapsed_ms: int | None = None
    calls: tuple[CallAttempt, ...] = ()


class ExtStatus(StrEnum):
    """query_by_key 的结果，只有三种（值 = 动态 / action-event-api 文档里的中文）。

    「仍无法确认」不是返回值，是人工对账的选项（→ ReconcileScheduler.reopen_reconcile）。
    """

    found_succeeded = "查到·成功"  # → on_reconciled(succeeded)
    found_not_executed = "查到·未执行"  # → on_reconciled(not_executed)
    not_found = "查无"  # 也计 1 次查询；一轮 3 次仍查无 → 转人工

    @property
    def outcome(self) -> "ReconcileOutcome | None":
        return {
            "查到·成功": ReconcileOutcome.succeeded,
            "查到·未执行": ReconcileOutcome.not_executed,
        }.get(self.value)


@dataclass(frozen=True)
class KeyQueryResult:
    ext_status: ExtStatus
    ext_ref: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


class ReconcileOutcome(StrEnum):
    """对账结论（与动态文档 reconcile result 一致；值 = 库里存的代码，label = 页面文字）。"""

    succeeded = "succeeded"  # 外部已成功 → 动作状态「成功」，补回执号
    not_executed = "not_executed"  # 外部未执行 → 「失败」，这条记录不可重新提交

    @property
    def label(self) -> str:
        return {"succeeded": "外部已成功", "not_executed": "外部未执行"}[self.value]

    @property
    def resubmittable(self) -> bool:
        return False


@dataclass(frozen=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    receipt_id: str | None = None
    detail: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class ActionSubmitter(Protocol):
    """Palantir专家实现（写回 / 外部系统适配）。必须按 idempotency_key 幂等。"""

    async def submit(self, action_run_id: str, idempotency_key: str) -> SubmitResult: ...


@runtime_checkable
class ReconcileQuery(Protocol):
    """Palantir专家实现：按幂等键查外部系统处理情况。"""

    async def query_by_key(self, idempotency_key: str) -> KeyQueryResult: ...


@runtime_checkable
class ReconcileScheduler(Protocol):
    """integration（Palantir专家）实现：人工选「仍无法确认」。round+1、tries=0、next_at=now；
    ext_system.idempotent=false 时保持 manual、next_at=null。不 commit。"""

    async def reopen_reconcile(self, action_run_id: str) -> None: ...


@runtime_checkable
class EventEmitter(Protocol):
    """integration 实现：发事件（ActionExecuted / ActionOutcomeUnknown 等）。

    event_id 由调用方（dynamic）生成。
    """

    async def emit(self, event_id: str, event_type: str, payload: dict[str, object]) -> None: ...


# 前端事件类型名（eventTypes section 的 id）
EVENT_ACTION_EXECUTED = "ActionExecuted"
EVENT_ACTION_OUTCOME_UNKNOWN = "ActionOutcomeUnknown"

# seed 里结果未知的对账任务（ACTIONS 20 + RUNS 54），自动轮次已用完、转人工
SEED_UNKNOWN_RECONCILE_TASKS = 74
SEED_RECONCILE_TRIES = 3


@runtime_checkable
class ReconciliationListener(Protocol):
    """动态本体负责人实现：更新 ActionRun 状态、时间线、触发后续自动化。"""

    async def on_reconciled(self, action_run_id: str, result: ReconcileResult) -> None: ...


def idempotency_key_for(action_run_id: str) -> str:
    """暂存时生成（前端 mock 格式）：act- + 小写动作记录号（去掉 "ACT-"）。

    >>> idempotency_key_for("ACT-20261025-3834")
    'act-20261025-3834'
    """
    rid = action_run_id[4:] if action_run_id.upper().startswith("ACT-") else action_run_id
    return f"act-{rid.lower()}"


def split_retry_suffix(key: str) -> tuple[str, int]:
    """'act-20261025-3834-r2' → ('act-20261025-3834', 2)；没有后缀 → (key, 0)。"""
    head, sep, tail = key.rpartition("-r")
    if sep and head and tail.isdigit():
        return head, int(tail)
    return key, 0


def new_resubmit_key(previous_key: str) -> str:
    """用户重新提交（只从「失败」）：`<键去掉 -rN>-r<N+1>`。act-x → act-x-r1 → act-x-r2。

    自动重试**不换键**：适配器内用同一个键最多重试 ext_system.maxRetry 次。
    """
    base, n = split_retry_suffix(previous_key)
    return f"{base}-r{n + 1}"
