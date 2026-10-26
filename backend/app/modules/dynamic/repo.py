"""数据访问（继承 app.common.repository.BaseRepository）。"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.common.repository import BaseRepository
from app.core import clock
from app.core.enums import TimelineStatus
from app.db.dialects import get_adapter
from app.modules.admin.models import SysUser
from app.modules.dynamic.models import (
    ActionRun,
    ActionRunDailyCounter,
    ActionRunLog,
    Alert,
    AlertLog,
    AlertOp,
)
from app.modules.dynamic.rules_models import Scenario, ScenarioImpact


class ScenarioRepo(BaseRepository[Scenario]):
    model = Scenario


class ScenarioImpactRepo(BaseRepository[ScenarioImpact]):
    model = ScenarioImpact


async def recent_rule_hits(
    session: AsyncSession, *, until: datetime | None = None, rule_ids: set[int] | None = None
) -> dict[int, int]:
    """当前近7天实际保存的命中数；不把定义里的历史weekly快照当成滚动统计。"""
    from app.modules.dynamic.rules_models import RuleHit

    until = until or clock.now()
    if rule_ids is not None and not rule_ids:
        return {}
    statement = (
        select(RuleHit.rule_id, func.count())
        .where(RuleHit.at >= until - timedelta(days=7), RuleHit.at <= until)
        .group_by(RuleHit.rule_id)
    )
    if rule_ids is not None:
        statement = statement.where(RuleHit.rule_id.in_(rule_ids))
    rows = await session.execute(statement)
    return dict(rows.all())


class AlertRepo(BaseRepository[Alert]):
    model = Alert
    not_found_message = "告警不存在"

    async def by_codes(self, codes: Sequence[str]) -> list[Alert]:
        rows = (await self.session.scalars(select(Alert).where(Alert.code.in_(codes)))).all()
        return list(rows)

    async def logs(self, alert_id: int) -> list[AlertLog]:
        stmt = select(AlertLog).where(AlertLog.alert_id == alert_id).order_by(AlertLog.seq)
        return list((await self.session.scalars(stmt)).all())

    async def add_log(
        self,
        alert: Alert,
        *,
        at: datetime,
        actor: str,
        op: AlertOp,
        text: str,
        from_status: str | None = None,
        to_status: str | None = None,
        abnormal: bool = False,
    ) -> AlertLog:
        seq = await self.session.scalar(
            select(func.coalesce(func.max(AlertLog.seq), 0)).where(AlertLog.alert_id == alert.id)
        )
        log = AlertLog(
            alert_id=alert.id,
            seq=int(seq or 0) + 1,
            at=at,
            actor=actor,
            op=op,
            text=text,
            from_status=from_status,
            to_status=to_status,
            abnormal=abnormal,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def people(self) -> list[str]:
        stmt = (
            select(SysUser.name)
            .where(SysUser.alert_assignable.is_not(None))
            .order_by(SysUser.alert_assignable, SysUser.id)
        )
        return list((await self.session.scalars(stmt)).all())


class ActionRunRepo(BaseRepository[ActionRun]):
    model = ActionRun
    not_found_message = "动作不存在"

    async def logs(self, run_id: int) -> list[ActionRunLog]:
        stmt = select(ActionRunLog).where(ActionRunLog.action_run_id == run_id).order_by(ActionRunLog.seq)
        return list((await self.session.scalars(stmt)).all())

    async def add_log(
        self,
        run: ActionRun,
        *,
        at: datetime,
        text: str,
        actor: str | None = None,
        level: TimelineStatus | None = None,
    ) -> ActionRunLog:
        await self.session.execute(select(ActionRun.id).where(ActionRun.id == run.id).with_for_update())
        seq = await self.session.scalar(
            select(func.coalesce(func.max(ActionRunLog.seq), 0)).where(ActionRunLog.action_run_id == run.id)
        )
        log = ActionRunLog(
            action_run_id=run.id, seq=int(seq or 0) + 1, at=at, text=text, actor=actor, level=level
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def by_idem_key(self, key: str) -> list[ActionRun]:
        stmt = select(ActionRun).where(ActionRun.idem_key == key).order_by(ActionRun.id)
        return list((await self.session.scalars(stmt)).all())

    async def next_code(self, day: str) -> str:
        """按日预留编号；含历史最大号，已发号不随业务回滚复用，日锁不跨外部调用。"""
        prefix = f"ACT-{day}-"
        codes = (
            await self.session.scalars(select(ActionRun.code).where(ActionRun.code.startswith(prefix)))
        ).all()
        nums = [int(c[len(prefix) :]) for c in codes if c[len(prefix) :].isdigit()]
        floor = max(nums, default=0)
        # 请求已占主池连接；独立无池连接避免并发请求互等第二条主池连接。
        engine = create_async_engine(self.session.get_bind().engine.url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as allocation, allocation.begin():
                await get_adapter(allocation).xact_lock(allocation, f"dynamic.action_run.code.{day}")
                counter = await allocation.scalar(
                    select(ActionRunDailyCounter).where(ActionRunDailyCounter.day == day)
                )
                if counter is None:
                    counter = ActionRunDailyCounter(day=day, last_value=floor)
                    allocation.add(counter)
                counter.last_value = max(counter.last_value, floor) + 1
                await allocation.flush()
                number = counter.last_value
        finally:
            await engine.dispose()
        return f"{prefix}{number:04d}"
