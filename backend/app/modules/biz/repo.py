"""数据访问。"""

from sqlalchemy import Select, select

from app.common.repository import BaseRepository
from app.modules.biz.models import BizOrder, OrderStatus, Store


class OrderRepo(BaseRepository[BizOrder]):
    model = BizOrder
    not_found_message = "订单不存在"

    def list_query(
        self, *, store: str | None = None, status: OrderStatus | None = None, channel: str | None = None
    ) -> Select[tuple[BizOrder]]:
        stmt = select(BizOrder).order_by(BizOrder.created_at_biz.desc(), BizOrder.id.desc())
        if store:
            stmt = stmt.where(
                BizOrder.store_id == select(Store.id).where(Store.code == store).scalar_subquery()
            )
        if status:
            stmt = stmt.where(BizOrder.status == status)
        if channel:
            stmt = stmt.where(BizOrder.channel == channel)
        return stmt

    def by_code_query(self, code: str) -> Select[tuple[BizOrder]]:
        """mock 订单号有重复：取 id 最小的一条。"""
        return select(BizOrder).where(BizOrder.code == code).order_by(BizOrder.id).limit(1)
