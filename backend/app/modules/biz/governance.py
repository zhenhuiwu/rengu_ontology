"""biz 表的行级过滤登记（路由的 apply_row_filter 和问数 sql_guard 共用）。

- 对象类型 → 表 / 属性 → 列（白名单）：store → store，order → biz_order；
- 一跳关系取前端 mock links.ts:74 的 order_belongs_to_store（order.store_id = store.store_id）。
  静态模块把关系定义入库后，改成从当前版本快照读（link_paths_from_definitions 不变）；
- 权限范围里的中文属性：大区 → store.big_region；订单经 order_belongs_to_store.big_region 一跳过滤；
- order_line / product 没有登记：问数直接查 order_line 会被 sql_guard 拒绝（order_line → 门店要两跳）。
"""

from app.common.row_filter import LinkPath, ObjectTable, link_paths_from_definitions
from app.common.sql_guard import GovernedTable, register_governed
from app.modules.biz.models import BizOrder, Store

S, OC = Store.__table__.c, BizOrder.__table__.c

STORE_COLUMNS = {
    "store_id": S.id,
    "code": S.code,
    "name": S.name,
    "city": S.city,
    "region": S.region,
    "big_region": S.big_region,
    "format": S.format,
    "status": S.status,
    "is_test": S.is_test,
}
ORDER_COLUMNS = {
    "order_id": OC.code,
    "store_id": OC.store_id,
    "channel": OC.channel,
    "status": OC.status,
    "created_at": OC.created_at_biz,
    "shipped_at": OC.shipped_at,
    "warehouse": OC.warehouse,
    "is_test": OC.is_test,
}
OBJECTS = {
    "store": ObjectTable(Store.__table__, STORE_COLUMNS),
    "order": ObjectTable(BizOrder.__table__, ORDER_COLUMNS),
}
# 前端 mock src/mock/links.ts:74（只取行级过滤需要的字段）
LINK_DEFS = [
    {
        "id": "order_belongs_to_store",
        "from": "order",
        "to": "store",
        "fromProp": "store_id",
        "toProp": "store_id",
    }
]
ORDER_LINKS: dict[str, LinkPath] = link_paths_from_definitions("order", LINK_DEFS, OBJECTS)

STORE_GOV = GovernedTable(
    Store.__table__, "store", STORE_COLUMNS, {}, {"大区": "big_region", "区域": "region"}
)
ORDER_GOV = GovernedTable(
    BizOrder.__table__,
    "order",
    ORDER_COLUMNS,
    ORDER_LINKS,
    {"大区": "order_belongs_to_store.big_region", "区域": "order_belongs_to_store.region"},
)

register_governed(STORE_GOV, ORDER_GOV)
