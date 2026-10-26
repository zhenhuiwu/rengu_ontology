"""真实订单日趋势：业务时区、截止、缺日补零和权限入口。"""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import false, select, update

from app.core import clock
from app.modules.biz.models import BizOrder, OrderStatus
from app.modules.dashboard import service
from tests.test_dashboard import seeded  # noqa: F401

URL = "/api/biz/v1/dashboard/sales/daily"


async def test_sales_daily_uses_orders_and_shanghai_days(client, seeded):  # noqa: F811
    orders = (await seeded.scalars(select(BizOrder.id).order_by(BizOrder.id).limit(5))).all()
    samples = [
        ("2020-01-01T15:59:00+00:00", "113"),
        ("2020-01-01T16:00:00+00:00", "226"),
        ("2019-12-31T15:59:00+00:00", "999"),
        ("2020-01-01T16:31:00+00:00", "999"),
        ("2020-01-01T12:00:00+00:00", "113"),
    ]
    for id_, (at, amount) in zip(orders, samples, strict=True):
        await seeded.execute(
            update(BizOrder)
            .where(BizOrder.id == id_)
            .values(
                created_at_biz=datetime.fromisoformat(at),
                amount=Decimal(amount),
                status=OrderStatus.cancelled,
            )
        )
    body = (await client.get(URL, params={"cutoff": "2020-01-02 00:30", "days": 3})).json()
    assert body["items"] == [
        {"date": "2019-12-31", "value": pytest.approx(999 / 1.13, abs=0.005)},
        {"date": "2020-01-01", "value": 200},
        {"date": "2020-01-02", "value": 200},
    ]
    assert body["dataAsOf"] == "2020-01-02 00:30"
    assert body["taxMode"] == "excl" and body["taxRate"] == 0.13


async def test_sales_daily_empty_and_permissions(client, seeded, monkeypatch):  # noqa: F811
    calls = []

    async def deny(stmt, user, object_type, columns, links):
        calls.append(object_type)
        return stmt.where(false())

    monkeypatch.setattr(service, "apply_row_filter", deny)
    response = await client.get(URL, params={"days": 2})
    assert response.status_code == 200, response.text
    assert calls == ["order"]
    assert len(response.json()["items"]) == 2
    assert all(r["value"] == 0 for r in response.json()["items"])
    assert response.json()["dataAsOf"] == "2026-10-25 15:42"


@pytest.mark.parametrize("params", [{"days": 0}, {"days": 91}, {"cutoff": "bad"}])
async def test_sales_daily_validates_params(client, users, params):
    response = await client.get(URL, params=params)
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_FAILED"


async def test_sales_daily_auth(client, users):
    response = await client.get(URL, headers={"X-Demo-As": "unknown"})
    assert response.status_code == 401


async def test_default_cutoff_includes_new_orders(client, seeded):  # noqa: F811
    id_ = await seeded.scalar(select(BizOrder.id).limit(1))
    await seeded.execute(
        update(BizOrder)
        .where(BizOrder.id == id_)
        .values(
            created_at_biz=clock.now(),
            amount=Decimal("113.00"),
        )
    )
    response = await client.get(URL, params={"days": 1})
    assert response.status_code == 200, response.text
    today = response.json()["items"][0]
    assert today["date"] == "2026-10-25" and today["value"] >= 100
    assert response.json()["dataAsOf"] == "2026-10-25 15:42"


async def test_default_orders_and_ranking_exclude_future(client, seeded):  # noqa: F811
    root = "/api/biz/v1"
    before_orders = (await client.get(f"{root}/orders")).json()["total"]
    before_count = sum(
        r["cnt"] for r in (await client.get(f"{root}/stores/ranking", params={"size": 200})).json()["items"]
    )
    order = await seeded.scalar(select(BizOrder).where(BizOrder.created_at_biz <= clock.now()).limit(1))
    future = clock.now() + timedelta(days=1)
    await seeded.execute(update(BizOrder).where(BizOrder.id == order.id).values(created_at_biz=future))
    after_orders = await client.get(f"{root}/orders")
    after_ranking = await client.get(f"{root}/stores/ranking", params={"size": 200})
    assert after_orders.json()["total"] == before_orders - 1
    assert sum(r["cnt"] for r in after_ranking.json()["items"]) == before_count - 1
    assert after_orders.json()["dataAsOf"] == after_ranking.json()["dataAsOf"] == "2026-10-25 15:42"
    # 显式未来 cutoff/to 沿用原接口含上界的语义，不因默认修复而被悄悄收至 now。
    cutoff = future.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    explicit = await client.get(f"{root}/orders", params={"cutoff": cutoff, "kw": order.code})
    assert explicit.json()["total"] == 1
    explicit_rank = await client.get(f"{root}/stores/ranking", params={"to": cutoff, "size": 200})
    assert sum(r["cnt"] for r in explicit_rank.json()["items"]) >= before_count
