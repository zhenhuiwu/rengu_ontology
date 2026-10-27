"""One explicitly authorized metric UI fixture in the QA test database; no reseeding or permission writes."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

METRIC = "uicheck_order_paid"
CHANNEL = "UICHECK_METRIC_20261027"
FORMULA = "SUM(SalesOrder.paid_amount)"
DEFINITION = [
    {"k": "时间字段", "v": "SalesOrder.created_at"},
    {"k": "默认过滤", "v": f"SalesOrder.channel = '{CHANNEL}'；SalesOrder.status <> 已取消"},
]


async def prepare_metric(session):
    assert session.get_bind().engine.url.database == "rengu_test_use_qa_20261027"
    from app.common.auth import resolve_user
    from app.common.ontology import set_ontology_scope
    from app.common.row_filter import is_deny_all
    from app.core import clock
    from app.modules.biz.models import BizOrder, OrderStatus, Store, StoreStatus
    from app.modules.static.metric_trial import evaluate_metric, metric_readiness
    from app.modules.static.models import Metric, MetricStatus, ObjectStatus, ObjectType

    await set_ontology_scope(session, 1)
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == "order"))
    assert obj is not None and obj.status == ObjectStatus.published
    # Verify the existing approved mappings instead of changing shared object definitions.
    expected = {
        "paid_amount": "amount",
        "created_at": "created_at_biz",
        "status": "status",
        "channel": "channel",
    }
    for api, column in expected.items():
        prop = next(p for p in obj.properties if p.api_name == api)
        assert prop.backing_column == column and not prop.is_sensitive and prop.visibility != "hidden"
    actor = await resolve_user(session, "U010001")
    assert actor.func.get("metrics") in ("view", "manage")
    assert not is_deny_all(actor.row_filters.get("order", {"deny": True}))
    store = await session.scalar(select(Store).where(Store.code == "UICHECK-METRIC-STORE"))
    if store is None:
        store = Store(
            code="UICHECK-METRIC-STORE",
            store_no="UICHECK-METRIC-STORE",
            name="试算核验门店",
            city="上海",
            region="华东一区",
            big_region="华东",
            format="测试",
            opened_at=clock.now().date(),
            status=StoreStatus.open,
        )
        session.add(store)
        await session.flush()
    tz = ZoneInfo("Asia/Shanghai")
    today = clock.now().astimezone(tz).date()
    for index, (days, amount, status) in enumerate(
        [
            (2, "10.20", OrderStatus.shipped),
            (1, "30.40", OrderStatus.shipped),
            (1, "999.00", OrderStatus.cancelled),
        ]
    ):
        code = f"UICHECK-METRIC-ORDER-{index}"
        row = await session.scalar(select(BizOrder).where(BizOrder.code == code))
        if row is None:
            session.add(
                BizOrder(
                    code=code,
                    store_id=store.id,
                    channel=CHANNEL,
                    status=status,
                    created_at_biz=datetime.combine(today - timedelta(days=days), time(12), tz),
                    amount=Decimal(amount),
                )
            )
        else:
            assert row.store_id == store.id and row.channel == CHANNEL
            assert row.status == status and row.amount == Decimal(amount)
    metric = await session.scalar(select(Metric).where(Metric.code == METRIC))
    if metric is None:
        metric = Metric(
            code=METRIC,
            api_name="UiCheckOrderPaid",
            display_name="UICHECK 订单实付合计",
            metric_group="UI核验",
            unit="元",
            version="v1",
            status=MetricStatus.published,
            owner=actor.name,
            formula=FORMULA,
            def_rows=DEFINITION,
            dims=[],
        )
        session.add(metric)
    else:
        assert metric.formula == FORMULA and metric.def_rows == DEFINITION
        assert metric.status == MetricStatus.published
    await session.flush()
    assert (await metric_readiness(session, actor, METRIC))["ready"] is True
    result = await evaluate_metric(session, actor, METRIC)
    assert [Decimal(row["value"]) for row in result["rows"]] == [Decimal("10.20"), Decimal("30.40")]
    return {"metric": METRIC, "rows": result["rows"], "scope": result["scope"]}
