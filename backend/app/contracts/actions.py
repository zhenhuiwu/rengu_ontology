"""契约 2：动态本体（动作中心 / 自动化）↔ 动作执行与对账（写回）。

规则（团队约定）：
1. 幂等键在「暂存」（生成待确认的 ActionRun）时生成并落库，之后不再变化；
2. 提交：`ActionSubmitter.submit(action_run_id, idempotency_key)` → SubmitResult
   - SUCCESS：外部系统确认成功，带回执号 receipt_id；
   - FAILED：确定失败（可以重新提交）；
   - UNKNOWN：超时等结果未知 —— **不得自动重试**，等待对账；
3. 对账结束时执行方回调 `ReconciliationListener.on_reconciled(action_run_id, result)`；
4. 重新提交 = 新建一个 ActionRun + 新幂等键（`new_resubmit_key`），旧记录保留不改。
   这和前端 mock 的 `-r1/-r2` 后缀一致。
5. 对账查询 `ReconcileQuery.query_by_key(idempotency_key)`：定时对账和人工对账都走它，不直连外部系统。

字段与 ui-vue/docs/backend/action-event-api.md §1.1 对齐：result=成功|失败|结果未知 → SubmitStatus 的 label；
ext_ref → receipt_id；code、retries、elapsed_ms、calls[] 为可选补充。
"""

import uuid
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
    """query_by_key 的结果（值即前端/文档里的中文）。"""

    processed = "已处理"
    not_processed = "未处理"
    not_found = "查无"
    unsupported = "不支持查询"


@dataclass(frozen=True)
class KeyQueryResult:
    ext_status: ExtStatus
    ext_ref: str | None = None
    raw: dict[str, object] = field(default_factory=dict)


class ReconcileOutcome(StrEnum):
    APPLIED = "APPLIED"  # 对账确认已生效 → 动作状态「已对账」/「成功」
    NOT_APPLIED = "NOT_APPLIED"  # 确认未生效 → 「失败」，允许重新提交
    MISMATCH = "MISMATCH"  # 生效但结果与预期不符 → 需人工处理


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
class ReconciliationListener(Protocol):
    """动态本体负责人实现：更新 ActionRun 状态、时间线、触发后续自动化。"""

    async def on_reconciled(self, action_run_id: str, result: ReconcileResult) -> None: ...


def new_idempotency_key(action_type: str) -> str:
    """暂存时生成。格式：<动作类型>-<随机>，例如 replenish-7f3a9c2e1b4d。"""
    return f"{action_type}-{uuid.uuid4().hex[:12]}"


def new_resubmit_key(original_key: str, attempt: int) -> str:
    """重新提交时生成新键：<原键去掉 -rN>-r<attempt>（attempt 从 1 开始）。"""
    base = original_key.rsplit("-r", 1)[0] if _has_retry_suffix(original_key) else original_key
    return f"{base}-r{attempt}"


def _has_retry_suffix(key: str) -> bool:
    head, sep, tail = key.rpartition("-r")
    return bool(sep and head and tail.isdigit())
