"""仅QA评测的专用Procrastinate worker：python -m app.modules.qa.eval.worker。"""

import argparse
import asyncio
import sys

import procrastinate

from app.core.config import get_settings
from app.db import models as _models  # noqa: F401  独立进程也必须完整登记ORM关联模型。
from app.db.session import dispose_engine
from app.modules.qa.eval import job
from app.modules.qa.eval.queue import LOCK, QUEUE, TASK


def build_app() -> procrastinate.App:
    url = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    worker = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=url))
    worker.task(name=TASK, queue=QUEUE, lock=LOCK)(run)
    return worker


async def run(run_code: str) -> None:
    await job._background(run_code)


app = build_app()  # schema CLI使用此入口，不登记任何其他空桩或周期任务。


async def main(install_schema: bool = False) -> None:
    if install_schema:
        async with app.open_async():
            row = await app.connector.execute_query_one_async(
                query="SELECT to_regclass('procrastinate_jobs') IS NOT NULL AS installed"
            )
            if not row["installed"]:
                await app.schema_manager.apply_schema_async()
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
