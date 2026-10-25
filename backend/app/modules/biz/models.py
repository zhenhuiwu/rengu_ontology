"""演示业务数据：门店 / 商品 / 订单 / 订单行（来源：前端 mock ALL_STORES、PRODUCTS、ORDERS）。

新增模型要在 app/db/models.py 里 import。主键都是 id bigint；前端编号放 code。

- 不建会员表（mock 只有脱敏手机号，留在 biz_order.member_phone）；
- 不建 store_sales（objects-qa.ts:60 是随机生成的 9 月数据，不能从订单推出来）；
- 问数 sql_guard 的受治理表登记见 app/modules/biz/governance.py。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, query_expression, relationship

from app.core.enums import LabelEnum
from app.db.base import Base, CodeMixin, IdMixin, MutableTableMixin, unique_except_seed
from app.db.types import enum_column


class StoreStatus(LabelEnum):
    open = "营业中"
    renovating = "装修中"
    closed = "已闭店"


class OrderStatus(LabelEnum):
    pending_payment = "待支付"
    picking = "拣货中"
    pending_shipment = "待出库"
    shipped = "已出库"
    delivered = "已签收"
    cancelled = "已取消"


class Store(CodeMixin, MutableTableMixin, Base):
    """门店。code = mock id（SH0031），store_no = mock code（SH-PD-0031）。"""

    __tablename__ = "store"
    __table_args__ = ({"comment": "门店"},)

    store_no: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, comment="门店编码（SH-PD-0031）"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(32), nullable=False, index=True, comment="区域（华东一区）")
    big_region: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True, comment="[推断] 大区，由区域推出（华东一区 → 华东）"
    )
    format: Mapped[str] = mapped_column(String(32), nullable=False, comment="业态（社区店/商场店/写字楼店）")
    manager: Mapped[str | None] = mapped_column(String(64), comment="店长姓名（mock 原文，不是用户外键）")
    area: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), comment="面积 ㎡（mock 有 6 店为空）")
    opened_at: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[StoreStatus] = mapped_column(enum_column(StoreStatus, 16), nullable=False)
    is_test: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="[推断] 是否测试店（metricCatalog.ts:33 gmv 过滤 门店.是否测试店；"
        "mock 无数据，seed 全 false）",
    )


class Product(CodeMixin, MutableTableMixin, Base):
    """商品。code = sku。"""

    __tablename__ = "product"
    __table_args__ = ({"comment": "商品"},)

    barcode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    spec: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True, comment="品类路径（生鲜/水果）"
    )
    brand: Mapped[str | None] = mapped_column(String(64))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)


class BizOrder(IdMixin, MutableTableMixin, Base):
    """订单。code = mock id（SO…）。

    mock 里有 2 个订单号重复（SO202610240889017、SO202610220914198，门店/状态不同），两条都保留：
    code 只对非 seed 行唯一（unique_except_seed）；seed upsert 键 = (code, created_at_biz)。
    按 code 查详情时取 id 最小的一条（seed 按文件顺序写入）。
    """

    __tablename__ = "biz_order"
    __table_args__ = (
        unique_except_seed("biz_order", "code"),
        UniqueConstraint("code", "created_at_biz", name="uq_biz_order_code_created_at_biz"),
        {"comment": "订单"},
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="订单号（前端 id）")
    store_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("store.id"), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus, 24), nullable=False, index=True)
    created_at_biz: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True, comment="下单时间（mock createdAt，带秒）"
    )
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="出库时间（带秒）")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, comment="实付 = 行合计 − 优惠")
    discount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0, server_default="0")
    warehouse: Mapped[str | None] = mapped_column(String(64))
    waybill: Mapped[str | None] = mapped_column(String(64))
    member_phone: Mapped[str | None] = mapped_column(String(16), comment="会员手机号（已脱敏 160****2039）")
    is_test: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="是否测试单（本体属性 objectProps.ts:45 is_test；seed 全 false）",
    )

    store: Mapped[Store] = relationship(lazy="joined")
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", order_by="OrderLine.line_no", lazy="selectin"
    )
    # 接口 lineCount（首页“商品数”= lines.length，Home.vue:54/144）。不是库列：由 OrderRepo 用
    # with_expression 填一个关联子查询 count(order_line)，列表一条 SQL 取出，不加载 lines。
    line_count: Mapped[int | None] = query_expression()


class OrderLine(IdMixin, MutableTableMixin, Base):
    """订单行。mock 同一订单里 sku 可重复（347 单），所以按 (order_id, line_no) 唯一，不按 sku。"""

    __tablename__ = "order_line"
    __table_args__ = (
        UniqueConstraint("order_id", "line_no", name="uq_order_line_order_id_line_no"),
        {"comment": "订单行"},
    )

    order_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("biz_order.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="行号（mock lines 顺序，从 1 起）")
    product_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("product.id"), nullable=False, index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, comment="数量（kg 商品 3 位小数）")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[BizOrder] = relationship(back_populates="lines")
    product: Mapped[Product] = relationship(lazy="joined")
