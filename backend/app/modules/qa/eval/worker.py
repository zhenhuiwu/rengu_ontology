"""仅QA评测的专用Procrastinate worker：python -m app.modules.qa.eval.worker。

默认禁用每日模型评测，遗留 daily 待办/运行任务会阻止启动且保持原状。
启动前还须确认没有其他活跃周期 worker，以免检查后再次并发入队 daily。
"""

import argparse
import asyncio
import sys

import procrastinate
from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401  独立进程也必须完整登记ORM关联模型。
from app.db.session import dispose_engine
from app.jobs.scope import scoped_sessions, workspace_ids
from app.modules.qa import eval_gen
from app.modules.qa.eval import daily, job
from app.modules.qa.eval.queue import LOCK, QUEUE, TASK
from app.modules.qa.models import QaEvalRun, RunStatus


def build_app(*, enable_daily: bool = False) -> procrastinate.App:
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    worker = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    worker.task(name=TASK, queue=QUEUE, lock=LOCK)(run)
    eval_gen.register(worker, QUEUE)  # 每日按本体自动出评测题（同队列，独立锁）
    if enable_daily:
        daily.register(worker, QUEUE)
    return worker


async def run(run_code: str, ontology_id: int | None = None) -> None:
    from app.common.ontology import WorkspaceInactive
    try:
        await job._background(run_code, ontology_id=ontology_id)
    except WorkspaceInactive:
        return


async def daily_run_pending() -> bool:
    for ontology_id in await workspace_ids():
        from app.common.ontology import WorkspaceInactive
        try:
            async with scoped_sessions(ontology_id)() as session:
                if await session.scalar(
                    select(QaEvalRun.id)
                    .where(
                        QaEvalRun.status.in_((RunStatus.queued, RunStatus.running)),
                        or_(
                            QaEvalRun.execution_context["trigger"].as_string() == "daily",
                            QaEvalRun.trigger_label == "每日自动",
                        ),
                    )
                    .limit(1)
                ):
                    return True
        except WorkspaceInactive:
            continue
    return False


# 请求会话在事务开始时降到 rengu_ontology_scoped（ontology_0018）；Procrastinate schema 若在那次迁移之后才装，
# 这个角色就没有队列表 / 函数的权限，评测入队会 503。装 schema 时补授权（幂等，已装也补）。
GRANT_RUNTIME = """
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rengu_ontology_scoped') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO rengu_ontology_scoped;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rengu_ontology_scoped;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO rengu_ontology_scoped;
  END IF;
END $$;
"""

app = build_app()  # 默认只登记手动评测与自动出题；每日模型评测须显式启用。


async def main(install_schema: bool = False, *, enable_daily: bool = False) -> None:
    worker = build_app(enable_daily=True) if enable_daily else app
    if install_schema:
        async with worker.open_async():
            row = await worker.connector.execute_query_one_async(
                query="SELECT to_regclass('procrastinate_jobs') IS NOT NULL AS installed"
            )
            if not row["installed"]:
                await worker.schema_manager.apply_schema_async()
            await worker.connector.execute_query_async(query=GRANT_RUNTIME)
        return
    recovery_started = False
    try:
        async with worker.open_async():
            if not enable_daily:
                row = await worker.connector.execute_query_one_async(
                    query="""SELECT EXISTS (
                        SELECT 1 FROM procrastinate_jobs
                        WHERE queue_name = 'qa-eval' AND task_name = 'qa.eval.daily'
                          AND status IN ('todo', 'doing')
                    ) AS blocked"""
                )
                if row["blocked"] or await daily_run_pending():
                    raise RuntimeError(
                        "每日自动评测已禁用，但存在待办/运行中的 qa.eval.daily 或其派生评测；"
                        "已拒绝启动且保留任务，请先核查其他周期 worker"
                    )
            async with job.worker_lifespan():
                recovery_started = True
                await job.recover_interrupted_runs()
                await worker.run_worker_async(queues=[QUEUE], concurrency=1, shutdown_graceful_timeout=5)
    finally:
        # 归属锁释放后回收取消或异常退出的运行，队列中的待运行任务保留。
        try:
            if recovery_started:
                await job.recover_interrupted_runs()
        finally:
            await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="仅消费QA评测队列；不启动其他模块空任务")
    parser.add_argument("--install-schema", action="store_true", help="仅初始化Procrastinate schema后退出")
    parser.add_argument("--enable-daily", action="store_true", help="显式启用每日自动模型评测（默认关闭）")
    arguments = parser.parse_args()
    if sys.platform == "win32":  # psycopg async不支持ProactorEventLoop。
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(install_schema=arguments.install_schema, enable_daily=arguments.enable_daily))
