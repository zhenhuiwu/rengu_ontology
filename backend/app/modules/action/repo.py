"""数据访问（动作类型）。"""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import select

from app.common.repository import BaseRepository
from app.modules.action.models import ActionRunDailyStat, ActionType, ActionTypeRule, ActionTypeVersion
from app.modules.integration.models import ExtSystem


class ActionTypeRepo(BaseRepository[ActionType]):
    model = ActionType
    not_found_message = "动作类型不存在"

    async def all_ordered(self) -> Sequence[ActionType]:
        """含已下线的（?ver= 历史视图按 retired_release_id 判断）。"""
        return (await self.session.scalars(select(ActionType).order_by(ActionType.id))).all()

    async def rule_action(self) -> dict[str, str]:
        """{规则编码: 规则命中生成的动作 id}（前端 RULE_ACTION；来自 action_type_rule.generates）。"""
        stmt = (
            select(ActionTypeRule.rule_code, ActionType.code)
            .join(ActionType, ActionType.id == ActionTypeRule.action_type_id)
            .where(ActionTypeRule.generates.is_(True))
        )
        return {rule: code for rule, code in (await self.session.execute(stmt)).all()}

    async def systems(self) -> dict[str, ExtSystem]:
        return {s.code: s for s in (await self.session.scalars(select(ExtSystem))).all()}

    async def versions(self, type_id: int) -> Sequence[ActionTypeVersion]:
        stmt = (
            select(ActionTypeVersion)
            .where(ActionTypeVersion.action_type_id == type_id)
            .order_by(ActionTypeVersion.at.desc(), ActionTypeVersion.id.desc())
        )
        return (await self.session.scalars(stmt)).all()

    async def run_daily(self, type_id: int, start: date, end: date) -> dict[date, ActionRunDailyStat]:
        stmt = select(ActionRunDailyStat).where(
            ActionRunDailyStat.action_type_id == type_id,
            ActionRunDailyStat.stat_date >= start,
            ActionRunDailyStat.stat_date <= end,
        )
        return {r.stat_date: r for r in (await self.session.scalars(stmt)).all()}
