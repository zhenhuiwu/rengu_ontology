"""后台任务桩（procrastinate）。

约定：
- 任务函数是普通 async 函数，签名定死；app/jobs/worker.py 用 procrastinate（3.10，锁在 uv.lock）
  把 JOBS 里的 8 个任务全部注册（`build_app()`），周期任务按 spec.cron 配 periodic。
  启动 worker：`uv run procrastinate --app=app.jobs.worker.app worker`（建表：`... schema --apply`）。
- 发布时定义表的展开**不是**异步任务：静态在发布事务里同步完成（CONVENTIONS §11）。
  原 release.expand 改为 release.after_commit：发布提交后只做可重做的派生刷新（问数缓存等）。
- 一期没有审计保留期清理：admin.cleanup_audit 保留注册但 enabled=False（不配周期、函数体不删数据）。
- **所有“现在 / 今天 / 近 N 天”都用 app.core.clock**，dev/test 下是 2026-10-25 15:42（北京时间）。
- 任务必须幂等（可能被重投）；业务写入在任务自己的事务里提交。
- 每个任务的实现归对应模块负责人，函数体里写 EXTENSION POINT。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
    enabled: bool = True  # False = 注册但不排周期（一期不用）


async def deliver_event(event_id: str) -> None:
    """事件投递给订阅方。失败记录次数并交给 redeliver_failed_events。"""
    # EXTENSION POINT (event 负责人)


async def redeliver_failed_events() -> int:
    """重投失败的事件（按退避时间，截止 clock.now()）。返回本轮重投条数。"""
    _due_before = clock.now()
    # EXTENSION POINT (event 负责人)
    return 0


async def dispatch_outbox(timestamp: int = 0) -> int:
    """Consume page subscription notifications; action/event workers own their other topics."""
    from app.modules.notifications.service import dispatch_subscriptions

    return await dispatch_subscriptions()


async def after_release_commit(ver: str) -> None:
    """发布**提交后**的派生刷新（问数缓存、搜索索引等，可重做）。定义表展开已在发布事务里同步完成。"""
    # EXTENSION POINT (static / qa 负责人)


async def reconcile_due() -> int:
    """周期 */15：扫 reconcile_task 里 next_at <= clock.now() 的任务，按幂等键查外部结果
    （contracts.actions.ReconcileQuery）。每轮最多 EXTRA_RECONCILE_ROUNDS 次尝试，用完 next_at=null 转人工，
    人工「再对账一轮」走 contracts.actions.ReconcileScheduler.reopen_reconcile。返回本轮处理条数。"""
    from app.modules.integration.worker import sweep

    return await sweep()


async def dispatch_actions() -> int:
    """Dispatch only explicitly queued new actions; claimed writes are never replayed."""
    from app.modules.integration.execution import dispatch_queued

    return await dispatch_queued()


async def export_job(export_id: str) -> None:
    """导出（列表 / 模型导出）生成文件。"""
    # EXTENSION POINT (各模块)


async def expire_api_keys() -> int:
    """把 expires_at <= clock.now() 的 API Key 置为已过期。返回条数。"""
    _now = clock.now()
    # EXTENSION POINT (admin / PDE)
    return 0


async def cleanup_audit_logs() -> int:
    """一期不做审计保留期清理（PDE 定）：任务停用，函数体不删任何数据。"""
    return 0


JOBS: list[JobSpec] = [
    JobSpec("event.deliver", "events", "event", deliver_event, description="事件投递"),
    JobSpec("event.redeliver_failed", "events", "event", redeliver_failed_events, "*/5 * * * *", "失败重投"),
    JobSpec("outbox.dispatch", "outbox", "PDE", dispatch_outbox, "* * * * *", "outbox 分发"),
    JobSpec(
        "release.after_commit", "release", "static", after_release_commit, description="发布提交后的派生刷新"
    ),
    JobSpec(
        "action.reconcile",
        "reconcile",
        "integration",
        reconcile_due,
        "*/15 * * * *",
        f"结果未知定时对账（扫 reconcile_task.next_at，每轮 {EXTRA_RECONCILE_ROUNDS} 次）",
    ),
    JobSpec("action.dispatch", "reconcile", "integration", dispatch_actions, "* * * * *", "持久动作执行队列"),
    JobSpec("export.run", "export", "各模块", export_job, description="导出"),
    JobSpec("admin.expire_api_keys", "maintenance", "admin", expire_api_keys, "0 * * * *", "API Key 过期"),
    JobSpec(
        "admin.cleanup_audit",
        "maintenance",
        "admin",
        cleanup_audit_logs,
        description="审计保留期清理（一期停用）",
        enabled=False,
    ),
]
