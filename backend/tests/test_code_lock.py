"""编号生成加锁（pg_advisory_xact_lock）：两个并发事务取下一个编号，后一个要等前一个提交，拿到的号不重复。

用两条独立连接（不走 conftest 的外层回滚事务），提交的数据在 finally 里删掉。
"""

import asyncio

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.modules.admin.models import SysRole
from app.modules.admin.repo import SysUserRepository
from app.modules.admin.roles import SysRoleRepository
from app.modules.data.repo import DatasourceRepo


async def test_role_code_serialized(engine):
    async with engine.connect() as c1, engine.connect() as c2:
        s1, s2 = AsyncSession(bind=c1), AsyncSession(bind=c2)
        code1 = None
        try:
            async with s1.begin():
                code1 = await SysRoleRepository(s1).next_role_code()
                task = asyncio.create_task(SysRoleRepository(s2).next_role_code())
                await asyncio.sleep(0.5)
                assert not task.done(), "第二个事务应该等锁"
                s1.add(
                    SysRole(
                        code=code1,
                        name="并发锁测试甲",
                        func={},
                        objs=[],
                        changed_at=clock.now(),
                        changed_by="t",
                    )
                )
            code2 = await asyncio.wait_for(task, 5)
            assert code2 != code1 and int(code2[2:]) == int(code1[2:]) + 1
            await s2.rollback()
        finally:
            async with s1.begin():
                await s1.execute(delete(SysRole).where(SysRole.name == "并发锁测试甲"))
            await s1.close()
            await s2.close()


@pytest.mark.parametrize(
    "next_fn",
    [lambda s: SysUserRepository(s).next_code(), lambda s: DatasourceRepo(s).max_code_no()],
    ids=["sys_user", "datasource"],
)
async def test_other_codes_wait_for_lock(engine, next_fn):
    async with engine.connect() as c1, engine.connect() as c2:
        s1, s2 = AsyncSession(bind=c1), AsyncSession(bind=c2)
        async with s1.begin():
            await next_fn(s1)
            task = asyncio.create_task(next_fn(s2))
            await asyncio.sleep(0.5)
            assert not task.done(), "第二个事务应该等锁"
        await asyncio.wait_for(task, 5)
        await s2.rollback()
        await s1.close()
        await s2.close()
