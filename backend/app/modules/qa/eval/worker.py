"""仅QA评测的专用Procrastinate worker：python -m app.modules.qa.eval.worker。"""

import argparse
import asyncio
import sys

import procrastinate

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401  独立进程也必须完整登记ORM关联模型。
from app.db.session import dispose_engine
from app.modules.qa import eval_gen
from app.modules.qa.eval import daily, job
from app.modules.qa.eval.queue import LOCK, QUEUE, TASK


def build_app() -> procrastinate.App:
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    worker = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    worker.task(name=TASK, queue=QUEUE, lock=LOCK)(run)
    eval_gen.register(worker, QUEUE)  # 每日按本体自动出评测题（同队列，独立锁）
    daily.register(worker, QUEUE)  # 每日按本体自动跑一轮评测（建运行并入队，由上面的 run 执行）
    return worker


async def run(run_code: str, ontology_id: int | None = None) -> None:
    await job._background(run_code, ontology_id=ontology_id)


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

app = build_app()  # schema CLI使用此入口；只登记评测运行、每日自动出题与每日自动评测，不登记其他模块任务。


async def main(install_schema: bool = False) -> None:
    if install_schema:
        async with app.open_async():
            row = await app.connector.execute_query_one_async(
                query="SELECT to_regclass('procrastinate_jobs') IS NOT NULL AS installed"
            )
            if not row["installed"]:
                await app.schema_manager.apply_schema_async()
            await app.connector.execute_query_async(query=GRANT_RUNTIME)
        return
    try:
        async with app.open_async(), job.worker_lifespan():
            await job.recover_interrupted_runs()
            await app.run_worker_async(queues=[QUEUE], concurrency=1, shutdown_graceful_timeout=5)
    finally:
        # 归属锁释放后回收取消或异常退出的运行，队列中的待运行任务保留。
        try:
            await job.recover_interrupted_runs()
        finally:
            await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="仅消费QA评测队列；不启动其他模块空任务")
    parser.add_argument("--install-schema", action="store_true", help="仅初始化Procrastinate schema后退出")
    arguments = parser.parse_args()
    if sys.platform == "win32":  # psycopg async不支持ProactorEventLoop。
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(install_schema=arguments.install_schema))
