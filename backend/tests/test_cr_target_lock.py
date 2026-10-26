"""不同既有CR不能并发占用同一定义，咨询锁覆盖检查至提交。"""

from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.static.cr_write import CrWriteService
from app.modules.static.models import ChangeRequest, CrTarget
from app.modules.static.schemas import CRCreateIn, TargetIn
from tests.test_creation_code_lock import _two_creates


async def test_existing_crs_serialize_same_target_until_commit(engine):
    actor = CurrentUser(
        id="target-lock-test", name=uuid4().hex, func={"releases": "manage", "metrics": "manage"}
    )
    target = TargetIn(kind="metric", id="metric_" + uuid4().hex)
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        crs = [
            await CrWriteService(setup).create(actor, CRCreateIn(title=f"占用{n}", reviewer=""))
            for n in (1, 2)
        ]
        await setup.commit()

    async def join(session, n):
        return await CrWriteService(session).add_target(crs[n - 1], actor, target)

    try:
        with pytest.raises(AppError) as error:
            await _two_creates(engine, join)
        assert error.value.code == "TARGET_IN_OTHER_CR"
        async with engine.connect() as check:
            assert (
                await check.scalar(
                    select(func.count())
                    .select_from(CrTarget)
                    .where(CrTarget.target_kind == target.kind, CrTarget.target_id == target.id)
                )
                == 1
            )
    finally:
        async with engine.begin() as cleanup:
            await cleanup.execute(delete(ChangeRequest).where(ChangeRequest.code.in_(crs)))
