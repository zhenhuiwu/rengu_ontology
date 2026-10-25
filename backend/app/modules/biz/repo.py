"""数据访问。"""

from datetime import datetime

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import raiseload, with_expression

from app.common.repository import BaseRepository
from app.modules.biz.models import BizOrder, OrderLine, OrderStatus, Store

LIKE_ESCAPE = "\\"


def like_contains(kw: str) -> str:
    """kw → ILIKE 的“包含”模式：转义 \\、%、_（用户输入按字面匹配），值走绑定参数。"""
    esc = (
        kw.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{esc}%"


def line_count_expr():
    """每单的订单行数：按 order_id 关联的标量子查询（和外层同一条 SQL，没有 N+1）。

    外层订单已经过行级过滤；order_line 不单独过滤，所以 lineCount = 可见订单的全部行，与详情 lines 一致。
    """
    return (
        select(func.count(OrderLine.id))
        .where(OrderLine.order_id == BizOrder.id)
        .correlate(BizOrder)
        .scalar_subquery()
    )


class OrderRepo(BaseRepository[BizOrder]):
    model = BizOrder
    not_found_message = "订单不存在"

    def list_query(
        self,
        *,
        store: str | None = None,
        status: OrderStatus | None = None,
        channel: str | None = None,
        kw: str | None = None,
        created_before: datetime | None = None,
        created_until: datetime | None = None,
    ) -> Select[tuple[BizOrder]]:
        """列表：不加载 lines（raiseload，误访问直接报错），lineCount 用子查询。

        - kw：订单号或门店名包含（不区分大小写，ILIKE + 转义）；
        - created_until：createdAt <= 该时刻；created_before：createdAt < 该时刻（分钟精度的截止时间用）。
        """
        stmt = (
            select(BizOrder)
            .options(raiseload(BizOrder.lines), with_expression(BizOrder.line_count, line_count_expr()))
            .order_by(BizOrder.created_at_biz.desc(), BizOrder.id.desc())
        )
        if store:
            stmt = stmt.where(
                BizOrder.store_id == select(Store.id).where(Store.code == store).scalar_subquery()
            )
        if status:
            stmt = stmt.where(BizOrder.status == status)
        if channel:
            stmt = stmt.where(BizOrder.channel == channel)
        if kw:
            pat = like_contains(kw)
            stmt = stmt.where(
                or_(
                    BizOrder.code.ilike(pat, escape=LIKE_ESCAPE),
                    BizOrder.store_id.in_(select(Store.id).where(Store.name.ilike(pat, escape=LIKE_ESCAPE))),
                )
            )
        if created_until is not None:
            stmt = stmt.where(BizOrder.created_at_biz <= created_until)
        if created_before is not None:
            stmt = stmt.where(BizOrder.created_at_biz < created_before)
        return stmt

    def by_code_query(self, code: str) -> Select[tuple[BizOrder]]:
        """mock 订单号有重复：取 id 最小的一条。"""
        return (
            select(BizOrder)
            .options(with_expression(BizOrder.line_count, line_count_expr()))
            .where(BizOrder.code == code)
            .order_by(BizOrder.id)
            .limit(1)
        )
