"""后台任务桩（procrastinate）。

约定：
- 任务函数是普通 async 函数，签名定死；接入 procrastinate 时由 PDE 在 app/jobs/worker.py 里
  `app.task(name=spec.name, queue=spec.queue)(spec.fn)` 注册，周期任务按 spec.cron 配 periodic。
  （骨架暂不引入 procrastinate 依赖，避免锁定版本。）
- **所有“现在 / 今天 / 近 N 天”都用 app.core.clock**，dev/test 下是 2026-10-25 15:42（北京时间）。
- 任务必须幂等（可能被重投）；业务写入在任务自己的事务里提交。
- 每个任务的实现归对应模块负责人，函数体里写 EXTENSION POINT。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from app.core import clock

# 对账：外部结果「仍无法确认」后，额外再对账的轮数（之后转人工）
EXTRA_RECONCILE_ROUNDS = 3


@dataclass(frozen=True)
class JobSpec:
    name: str
    queue: str
    owner: str
    fn: Callable[..., Awaitable[Any]]
    cron: str | None = None  # 周期任务；None = 由业务 defer
    description: str = ""


async def deliver_event(event_id: str) -> None:
    """事件投递给订阅方。失败记录次数并交给 redeliver_failed_events。"""
    # EXTENSION POINT (event 负责人)


async def redeliver_failed_events() -> int:
    """重投失败的事件（按退避时间，截止 clock.now()）。返回本轮重投条数。"""
    _due_before = clock.now()
    # EXTENSION POINT (event 负责人)
    return 0


async def dispatch_outbox() -> int:
    """outbox 表 → 投递（事件 / 回写调用）。返回本轮处理条数。"""
    # EXTENSION POINT (PDE)
    return 0


async def expand_release(ver: str) -> None:
    """发布后展开：把 ver 的快照展开到已发布定义表，刷新问数缓存等。发布事务提交后 defer。"""
    # EXTENSION POINT (static 负责人)


async def reconcile_unknown(action_run_id: str, round_no: int) -> None:
    """对 UNKNOWN 的提交按幂等键查外部结果（contracts.actions.ReconcileQuery）。
    结果为「仍无法确认」且 round_no < EXTRA_RECONCILE_ROUNDS 时再 defer 一轮；用完转人工。"""
    # EXTENSION POINT (integration / Palantir专家)


async def export_job(export_id: str) -> None:
    """导出（列表 / 模型导出）生成文件。"""
    # EXTENSION POINT (各模块)


async def expire_api_keys() -> int:
    """把 expires_at <= clock.now() 的 API Key 置为已过期。返回条数。"""
    _now = clock.now()
    # EXTENSION POINT (admin / PDE)
    return 0


AUDIT_RETENTION = timedelta(days=180)  # [推断] 保留期，PDE 定


async def cleanup_audit_logs() -> int:
    """删除早于 clock.now() - AUDIT_RETENTION 的审计日志。返回条数。"""
    _cutoff = clock.now() - AUDIT_RETENTION
    # EXTENSION POINT (admin / PDE)
    return 0


JOBS: list[JobSpec] = [
    JobSpec("event.deliver", "events", "event", deliver_event, description="事件投递"),
    JobSpec("event.redeliver_failed", "events", "event", redeliver_failed_events, "*/5 * * * *", "失败重投"),
    JobSpec("outbox.dispatch", "outbox", "PDE", dispatch_outbox, "* * * * *", "outbox 分发"),
    JobSpec("release.expand", "release", "static", expand_release, description="发布后展开"),
    JobSpec(
        "action.reconcile",
        "reconcile",
        "integration",
        reconcile_unknown,
        description=f"结果未知对账；仍无法确认后再对 {EXTRA_RECONCILE_ROUNDS} 轮",
    ),
    JobSpec("export.run", "export", "各模块", export_job, description="导出"),
    JobSpec("admin.expire_api_keys", "maintenance", "admin", expire_api_keys, "0 * * * *", "API Key 过期"),
    JobSpec(
        "admin.cleanup_audit", "maintenance", "admin", cleanup_audit_logs, "30 3 * * *", "审计日志保留期清理"
    ),
]
