"""outbox_event（基础层）+ sys_user（最小占位）+ ontology_release.published_by → published_by_id 外键
+ biz 演示业务表 store / product / biz_order / order_line

ontology_release 只追加、发布人从姓名改为外键无法可靠回填：已有发布记录时本迁移直接报错，
开发库请 `make db-reset` 重建后重新 seed（CONVENTIONS §9 规则 6）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _require_empty_releases() -> None:
    n = op.get_bind().execute(sa.text("SELECT count(*) FROM ontology_release")).scalar()
    if n:
        raise RuntimeError(
            f"ontology_release 已有 {n} 条记录：0002 把 published_by（姓名）改为 sys_user 外键，"
            "不能自动回填。开发库请 make db-reset 后重新 seed。"
        )


def upgrade() -> None:
    _require_empty_releases()
    op.create_table(
        "outbox_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False, comment="幂等键，订阅方去重"),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_event")),
        sa.UniqueConstraint("event_id", name=op.f("uq_outbox_event_event_id")),
        comment="事务性 outbox（写方同事务写入，dispatch_outbox 分发）",
    )
    op.create_index(
        "ix_outbox_event_pending",
        "outbox_event",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.create_index(op.f("ix_outbox_event_topic"), "outbox_event", ["topic"], unique=False)
    op.create_table(
        "product",
        sa.Column("barcode", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("spec", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False, comment="品类路径（生鲜/水果）"),
        sa.Column("brand", sa.String(length=64), nullable=True),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product")),
        sa.UniqueConstraint("code", name=op.f("uq_product_code")),
        comment="商品",
    )
    op.create_index(op.f("ix_product_barcode"), "product", ["barcode"], unique=False)
    op.create_index(op.f("ix_product_category"), "product", ["category"], unique=False)
    op.create_table(
        "store",
        sa.Column("store_no", sa.String(length=32), nullable=False, comment="门店编码（SH-PD-0031）"),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("city", sa.String(length=32), nullable=False),
        sa.Column("region", sa.String(length=32), nullable=False, comment="区域（华东一区）"),
        sa.Column(
            "big_region",
            sa.String(length=32),
            nullable=False,
            comment="[推断] 大区，由区域推出（华东一区 → 华东）",
        ),
        sa.Column("format", sa.String(length=32), nullable=False, comment="业态（社区店/商场店/写字楼店）"),
        sa.Column(
            "manager", sa.String(length=64), nullable=True, comment="店长姓名（mock 原文，不是用户外键）"
        ),
        sa.Column(
            "area", sa.Numeric(precision=10, scale=1), nullable=True, comment="面积 ㎡（mock 有 6 店为空）"
        ),
        sa.Column("opened_at", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "renovating",
                "closed",
                name="storestatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column(
            "is_test",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="[推断] 是否测试店（metricCatalog.ts:33 gmv 过滤 门店.是否测试店；mock 无数据，seed 全 false）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_store")),
        sa.UniqueConstraint("code", name=op.f("uq_store_code")),
        sa.UniqueConstraint("store_no", name=op.f("uq_store_store_no")),
        comment="门店",
    )
    op.create_index(op.f("ix_store_big_region"), "store", ["big_region"], unique=False)
    op.create_index(op.f("ix_store_city"), "store", ["city"], unique=False)
    op.create_index(op.f("ix_store_region"), "store", ["region"], unique=False)
    op.create_table(
        "sys_user",
        sa.Column("name", sa.String(length=64), nullable=False, comment="姓名（不唯一）"),
        sa.Column("account", sa.String(length=128), nullable=True, comment="登录账号（邮箱）"),
        sa.Column(
            "dept",
            sa.String(length=128),
            nullable=True,
            comment="部门（mock 原文，如「华东事业部 / 运营中心」）",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "disabled",
                "pending",
                name="userstatus",
                native_enum=False,
                create_constraint=True,
                length=16,
            ),
            server_default="active",
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False, comment="业务编号（前端 id）"),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_user")),
        sa.UniqueConstraint("account", name=op.f("uq_sys_user_account")),
        sa.UniqueConstraint("code", name=op.f("uq_sys_user_code")),
        comment="用户（最小占位，PDE 补全）",
    )
    op.create_index(op.f("ix_sys_user_name"), "sys_user", ["name"], unique=False)
    op.create_table(
        "biz_order",
        sa.Column("code", sa.String(length=64), nullable=False, comment="订单号（前端 id）"),
        sa.Column("store_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending_payment",
                "picking",
                "pending_shipment",
                "shipped",
                "delivered",
                "cancelled",
                name="orderstatus",
                native_enum=False,
                create_constraint=True,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at_biz",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="下单时间（mock createdAt，带秒）",
        ),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True, comment="出库时间（带秒）"),
        sa.Column(
            "amount", sa.Numeric(precision=12, scale=2), nullable=False, comment="实付 = 行合计 − 优惠"
        ),
        sa.Column("discount", sa.Numeric(precision=12, scale=2), server_default="0", nullable=False),
        sa.Column("warehouse", sa.String(length=64), nullable=True),
        sa.Column("waybill", sa.String(length=64), nullable=True),
        sa.Column(
            "member_phone", sa.String(length=16), nullable=True, comment="会员手机号（已脱敏 160****2039）"
        ),
        sa.Column(
            "is_test",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            comment="是否测试单（本体属性 objectProps.ts:45 is_test；seed 全 false）",
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["store_id"], ["store.id"], name=op.f("fk_biz_order_store_id_store")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_biz_order")),
        sa.UniqueConstraint("code", "created_at_biz", name="uq_biz_order_code_created_at_biz"),
        comment="订单",
    )
    op.create_index(op.f("ix_biz_order_channel"), "biz_order", ["channel"], unique=False)
    op.create_index(op.f("ix_biz_order_code"), "biz_order", ["code"], unique=False)
    op.create_index(op.f("ix_biz_order_created_at_biz"), "biz_order", ["created_at_biz"], unique=False)
    op.create_index(op.f("ix_biz_order_status"), "biz_order", ["status"], unique=False)
    op.create_index(op.f("ix_biz_order_store_id"), "biz_order", ["store_id"], unique=False)
    op.create_index(
        "uq_biz_order_code_nonseed",
        "biz_order",
        ["code"],
        unique=True,
        postgresql_where=sa.text("NOT is_seed"),
    )
    op.create_table(
        "order_line",
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False, comment="行号（mock lines 顺序，从 1 起）"),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "qty", sa.Numeric(precision=12, scale=3), nullable=False, comment="数量（kg 商品 3 位小数）"
        ),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_seed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["biz_order.id"], name=op.f("fk_order_line_order_id_biz_order")
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["product.id"], name=op.f("fk_order_line_product_id_product")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_line")),
        sa.UniqueConstraint("order_id", "line_no", name="uq_order_line_order_id_line_no"),
        comment="订单行",
    )
    op.create_index(op.f("ix_order_line_order_id"), "order_line", ["order_id"], unique=False)
    op.create_index(op.f("ix_order_line_product_id"), "order_line", ["product_id"], unique=False)
    op.add_column(
        "ontology_release",
        sa.Column("published_by_id", sa.BigInteger(), nullable=False, comment="发布人（sys_user.id）"),
    )
    op.create_foreign_key(
        op.f("fk_ontology_release_published_by_id_sys_user"),
        "ontology_release",
        "sys_user",
        ["published_by_id"],
        ["id"],
    )
    op.drop_column("ontology_release", "published_by")


def downgrade() -> None:
    _require_empty_releases()
    op.add_column(
        "ontology_release",
        sa.Column("published_by", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    )
    op.drop_constraint(
        op.f("fk_ontology_release_published_by_id_sys_user"), "ontology_release", type_="foreignkey"
    )
    op.drop_column("ontology_release", "published_by_id")
    op.drop_index(op.f("ix_order_line_product_id"), table_name="order_line")
    op.drop_index(op.f("ix_order_line_order_id"), table_name="order_line")
    op.drop_table("order_line")
    op.drop_index(
        "uq_biz_order_code_nonseed", table_name="biz_order", postgresql_where=sa.text("NOT is_seed")
    )
    op.drop_index(op.f("ix_biz_order_store_id"), table_name="biz_order")
    op.drop_index(op.f("ix_biz_order_status"), table_name="biz_order")
    op.drop_index(op.f("ix_biz_order_created_at_biz"), table_name="biz_order")
    op.drop_index(op.f("ix_biz_order_code"), table_name="biz_order")
    op.drop_index(op.f("ix_biz_order_channel"), table_name="biz_order")
    op.drop_table("biz_order")
    op.drop_index(op.f("ix_sys_user_name"), table_name="sys_user")
    op.drop_table("sys_user")
    op.drop_index(op.f("ix_store_region"), table_name="store")
    op.drop_index(op.f("ix_store_city"), table_name="store")
    op.drop_index(op.f("ix_store_big_region"), table_name="store")
    op.drop_table("store")
    op.drop_index(op.f("ix_product_category"), table_name="product")
    op.drop_index(op.f("ix_product_barcode"), table_name="product")
    op.drop_table("product")
    op.drop_index(op.f("ix_outbox_event_topic"), table_name="outbox_event")
    op.drop_index(
        "ix_outbox_event_pending",
        table_name="outbox_event",
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.drop_table("outbox_event")
