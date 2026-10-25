"""数据访问（继承 app.common.repository.BaseRepository）。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select

from app.common.repository import BaseRepository
from app.core.enums import TimelineStatus
from app.modules.admin.models import SysUser
from app.modules.dynamic.models import ActionRun, ActionRunLog, Alert, AlertLog, AlertOp


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
        """ACT-YYYYMMDD-NNNN：同日已有编号（含执行记录）的最大序号 + 1（前端 Actions.vue L145 的口径）。"""
        prefix = f"ACT-{day}-"
        codes = (
            await self.session.scalars(select(ActionRun.code).where(ActionRun.code.startswith(prefix)))
        ).all()
        nums = [int(c[len(prefix) :]) for c in codes if c[len(prefix) :].isdigit()]
        return f"{prefix}{(max(nums) if nums else 0) + 1:04d}"
