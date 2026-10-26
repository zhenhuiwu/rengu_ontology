"""外部系统新建编号并发（Palantir专家 r3）：两个事务同时新建，编号不撞（_next_code 先拿事务级咨询锁）。

不走 client / 外层回滚事务：两个独立连接各自提交，才能复现“同时取最大号 + 1”；结束时删掉本测试建的行。
"""

import asyncio

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.integration.models import ExtSystem
from app.modules.integration.schemas import ExtSystemIn
from app.modules.integration.service import ExtSystemService

BODY = {
    "name": "并发测试网关",
    "vendor": "阿里云",
    "category": "消息",
    "endpoint": "https://sms.example.com/api",
    "auth": "API Key",
    "authDetail": "请求头 X-Api-Key",
    "idempotent": True,
    "idemKey": "X-Idempotency-Key",
    "timeoutMs": 5000,
    "maxRetry": 2,
    "rateLimit": "50 次/秒",
    "owner": "陈立军",
}
NAMES = ["并发测试网关A", "并发测试网关B"]


async def test_concurrent_create_gets_distinct_codes(engine):
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as first,
            AsyncSession(engine, expire_on_commit=False) as second,
        ):
            pid = await second.scalar(text("SELECT pg_backend_pid()"))

            def body(name):
                return ExtSystemIn.model_validate({**BODY, "name": name})

            a = await ExtSystemService(first).create(body(NAMES[0]), actor="test")
            task = asyncio.create_task(ExtSystemService(second).create(body(NAMES[1]), actor="test"))
            try:
                # 确认确实等待取号锁后才提交，不靠 sleep 猜测调度时机。
                async with asyncio.timeout(5):
                    while not await first.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks "
                            "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
                        ),
                        {"pid": pid},
                    ):
                        if task.done():
                            await task
                            raise AssertionError("第二个事务没有等待取号锁")
                await first.commit()
                b = await asyncio.wait_for(task, 5)
                await second.commit()
                assert int(b.code[5:]) == int(a.code[5:]) + 1
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        async with AsyncSession(engine) as s, s.begin():
            await s.execute(delete(ExtSystem).where(ExtSystem.name.in_(NAMES)))
