"""外部系统新建编号并发（Palantir专家 r3）：两个事务同时新建，编号不撞（_next_code 先拿事务级咨询锁）。

不走 client / 外层回滚事务：两个独立连接各自提交，才能复现“同时取最大号 + 1”；结束时删掉本测试建的行。
"""

import asyncio

from sqlalchemy import delete
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


async def _create(engine, name: str, hold: float, started: asyncio.Event | None = None) -> str:
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        body = ExtSystemIn.model_validate({**BODY, "name": name})
        obj = await ExtSystemService(s).create(body, actor="test")
        if started:
            started.set()
        await asyncio.sleep(hold)  # 拿着锁不提交：没有锁时另一个事务此时读到的最大号相同
        return obj.code


async def test_concurrent_create_gets_distinct_codes(engine):
    try:
        started = asyncio.Event()
        a = asyncio.create_task(_create(engine, NAMES[0], 0.5, started))
        await started.wait()
        b = asyncio.create_task(_create(engine, NAMES[1], 0))
        codes = await asyncio.gather(a, b)
        assert codes[0] != codes[1]
        n = [int(c[5:]) for c in codes]
        assert n[1] == n[0] + 1, codes
    finally:
        async with AsyncSession(engine) as s, s.begin():
            await s.execute(delete(ExtSystem).where(ExtSystem.name.in_(NAMES)))
