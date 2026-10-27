"""在请求的asyncpg事务中原子入队；复用Procrastinate 3.10自带的版本化SQL函数。"""

import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import require_ontology_id
from app.core.errors import AppError

QUEUE = "qa-eval"
TASK = "qa.eval.run"
LOCK = "qa.eval.global"


async def defer(session: AsyncSession, run_code: str, *_unused) -> int:
    """借用同一请求连接；run、审计和queue job一起提交或回滚，无派发间隙。"""
    ontology_id = require_ontology_id(session)
    try:
        return int(
            await session.scalar(
                text("""
            SELECT unnest(procrastinate_defer_jobs_v1(ARRAY[
                ROW(:queue, :task, 0, :lock, :queue_lock, CAST(:args AS jsonb), NULL)
                ::procrastinate_job_to_defer_v1
            ]))
        """),
                {
                    "queue": QUEUE,
                    "task": TASK,
                    "lock": f"{LOCK}.{ontology_id}",
                    "queue_lock": f"qa.eval.{ontology_id}.{run_code}",
                    "args": json.dumps({"run_code": run_code, "ontology_id": ontology_id}),
                },
            )
        )
    except DBAPIError as exc:
        raise AppError(
            "评测队列不可用，请先部署Procrastinate队列schema",
            code="QA_EVAL_QUEUE_UNAVAILABLE",
            status_code=503,
        ) from exc


async def fail_interrupted(session: AsyncSession, job_id: int) -> None:
    """只有确认无人持有运行锁后才能调用，解除孤儿doing job的全局队列锁。"""
    await session.execute(text("SELECT procrastinate_finish_job_v1(:id, 'failed', false)"), {"id": job_id})
