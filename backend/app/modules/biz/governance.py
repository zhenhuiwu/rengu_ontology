"""biz 表的行级过滤登记（路由的 apply_row_filter 和问数 sql_guard 共用）。

- 对象类型 → 表 / 属性 → 列（白名单）：store → store，order → biz_order；
- 一跳关系取前端 mock links.ts:74 的 order_belongs_to_store（order.store_id = store.store_id）。
  静态模块把关系定义入库后，改成从当前版本快照读（link_paths_from_definitions 不变）；
- 权限范围里的中文属性：大区 → store.big_region；订单经 order_belongs_to_store.big_region 一跳过滤；
- order_line 经 order_id → biz_order 继承订单的行级权限（InheritVia，复用订单那一跳，
  实际是 order_line → biz_order → store 两跳；前端按订单所属大区控权限）；
- 订单.门店（static-api.md §1.5，静态 9c7fe84）：经 order_belongs_to_store 一跳比 `store.name`，
  范围值原样是门店名、不换 code；
- 商品.品类（同上）：product 登记为受控，谓词按类目路径第一段 `split_part(category, '/', 1)` 比，
  范围值先经 contracts.static.CATEGORY_SCOPE_ALIAS 换成一级类目 [推断]；
- 多条范围的合并（static 0cfb2eb §1.5，roles.ts:110-117）：granted 角色的全部范围摊平取 OR
  （同一角色内也 OR [推断]）；有查看权且没配该对象范围的角色（defaultDeny 的除外）→ 不限制；
  见 sql_guard.effective_expr。「门店」和「大区」不会 AND；
- order_line 只继承订单的范围，不按商品品类过滤（已定：静态，roles.ts:62 范围按对象类型配）。
"""

from sqlalchemy import func, literal_column

from app.common.row_filter import LinkPath, ObjectTable, link_paths_from_definitions
from app.common.sql_guard import GovernedTable, InheritVia, register_governed
from app.contracts.static import CATEGORY_SCOPE_ALIAS
from app.modules.biz.models import BizOrder, OrderLine, Product, Store

S, OC, LC, PC = Store.__table__.c, BizOrder.__table__.c, OrderLine.__table__.c, Product.__table__.c

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
    {
        "大区": "order_belongs_to_store.big_region",
        "区域": "order_belongs_to_store.region",
        "门店": "order_belongs_to_store.name",
    },
)

# 一级类目 = 类目路径第一段；'/'、1 写成字面量，谓词里只有范围值是绑定参数
PRODUCT_COLUMNS = {
    "sku": PC.code,
    "category": PC.category,
    "category_l1": func.split_part(PC.category, literal_column("'/'"), literal_column("1")),
    "brand": PC.brand,
}
PRODUCT_GOV = GovernedTable(
    Product.__table__,
    "product",
    PRODUCT_COLUMNS,
    field_of_prop={"品类": "category_l1"},
    value_alias={"品类": CATEGORY_SCOPE_ALIAS},
)

ORDER_LINE_GOV = GovernedTable(
    OrderLine.__table__,
    "order_line",
    {},
    inherit=InheritVia(local=LC.order_id, parent=ORDER_GOV, parent_key=OC.id),
)

register_governed(STORE_GOV, ORDER_GOV, ORDER_LINE_GOV, PRODUCT_GOV)
