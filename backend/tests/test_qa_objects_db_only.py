"""真实旧对象 API 的来源/范围回归：内存 catalog 不能补业务记录或分析结果。"""

import csv
import io

import pytest
from sqlalchemy import false, func, select

from app.modules.biz.models import BizOrder, OrderStatus, Store
from app.modules.qa.objects_api import _local
from tests.qa_access_support import seed_qa_access
from tests.use_objects_support import enable_fixture_export

B = "/api/qa/v1"


@pytest.fixture
async def qa(users, client):
    await seed_qa_access(users)
    await enable_fixture_export(users)
    await users.flush()
    return client


async def test_real_routes_never_read_synthetic_catalog(qa, users, monkeypatch):
    from app.modules.qa import objects_api

    original = objects_api.catalog.load
    forbidden = {
        "OBJ_EXTRA_STORES",
        "QA_EC_STORES",
        "REGION_EC",
        "QA_TOTAL_SALES",
        "QA_TOTAL_CUSTOMERS",
        "OBJ_SCOPE",
        "OBJ_CUTOFF",
        "QA_METRIC",
    }

    def checked(name):
        assert name not in forbidden, f"synthetic business source: {name}"
        return original(name)

    monkeypatch.setattr(objects_api.catalog, "load", checked)
    db_ids = set((await users.scalars(select(Store.code).where(Store.is_test.is_(False)))).all())
    body = (await qa.post(f"{B}/objects/store/search", json={"size": 200})).json()
    assert {x["id"] for x in body["items"]} == db_ids and body["total"] == len(db_ids)
    assert all("sales" not in x["cells"] for x in body["items"])
    assert (await qa.get(f"{B}/objects/store/CATALOG-ONLY-GHOST")).status_code == 404
    for suffix in ["", "/related", "/timeline", "/actions"]:
        assert (await qa.get(f"{B}/objects/store/SH0031{suffix}")).status_code == 200
    exported = await qa.post(f"{B}/objects/store/export", json={})
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert len(rows) - 1 == len(db_ids)
    assert all("销售" not in title for title in rows[0])
    assert await users.scalar(select(func.count()).select_from(Store).where(Store.is_test.is_(False))) == len(
        db_ids
    )


@pytest.mark.parametrize(
    "kind,question", [("store", "销售额合计"), ("order", "预测下月销售"), ("product", "历史销量对比")]
)
async def test_unavailable_analysis_never_returns_a_fake_zero_answer(qa, kind, question):
    response = await qa.post(
        f"{B}/scoped-ask", json={"scope": {"kind": kind, "ids": []}, "question": question}
    )
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "DATA_NOT_CONFIGURED"
    assert "未生成" in response.json()["message"] and "rows" not in response.json()


async def test_supported_summary_uses_visible_db_amounts_and_deduplicates_ids(qa, users):
    code = "SO202610250794580"
    order = await users.scalar(select(BizOrder).where(BizOrder.code == code))
    body = {"scope": {"kind": "order", "ids": [code, code, "NOPE"]}, "question": "实付金额合计"}
    response = await qa.post(f"{B}/scoped-ask", json=body)
    assert response.status_code == 200, response.text
    assert str(float(order.amount)) in response.json()["text"]
    assert "未套用指标口径" in response.json()["basis"] and "数据截至" not in response.json()["basis"]
    empty = await qa.post(f"{B}/scoped-ask", json=body | {"scope": {"kind": "order", "ids": []}})
    assert "暂无数据" in empty.json()["text"]
    product = await qa.post(
        f"{B}/scoped-ask", json={"scope": {"kind": "product", "ids": []}, "question": "平均零售价"}
    )
    assert product.status_code == 200 and "平均零售价 -- 元" in product.json()["text"]


async def test_store_kpis_and_order_timeline_are_verified_base_fields(qa, users):
    store = await users.scalar(select(Store).where(Store.code == "SH0031"))
    store.manager = "真实数据库店长"
    await users.flush()
    detail = (await qa.get(f"{B}/objects/store/SH0031")).json()
    assert "店长" not in {field["label"] for field in detail["fields"]}
    paid = await users.scalar(
        select(func.sum(BizOrder.amount)).where(
            BizOrder.store_id == store.id,
            BizOrder.is_test.is_(False),
            BizOrder.status.not_in([OrderStatus.cancelled, OrderStatus.pending_payment]),
        )
    )
    assert paid is not None and detail["kpis"] == []
    order = await users.scalar(select(BizOrder).where(BizOrder.code == "SO202610250794580"))
    timeline = (await qa.get(f"{B}/objects/order/{order.code}/timeline")).json()
    expected = {_local(order.created_at_biz), _local(order.shipped_at)} - {None}
    assert {r["time"] for r in timeline["items"]} == expected
    assert all(r["text"] not in ("支付成功", "用户取消") for r in timeline["items"])


async def test_existing_row_filter_still_denies_only_actual_out_of_scope_rows(qa, monkeypatch):
    async def deny(stmt, *_args):
        return stmt.where(false())

    monkeypatch.setattr("app.modules.qa.objects_api.apply_row_filter", deny)
    monkeypatch.setattr("app.modules.qa.explore_api.apply_row_filter", deny)
    body = (await qa.post(f"{B}/objects/store/search", json={})).json()
    assert body["total"] == 0 and body["items"] == []
    for suffix in ["", "/related", "/timeline", "/actions"]:
        response = await qa.get(f"{B}/objects/store/SH0031{suffix}")
        assert response.status_code == 404
    single = {"scope": {"kind": "store", "single": {"kind": "store", "id": "SH0031"}}, "question": "销售额"}
    assert (await qa.post(f"{B}/scoped-ask", json=single)).status_code == 403
    assert (await qa.get(f"{B}/objects/store/CATALOG-ONLY-GHOST")).status_code == 404
    region = await qa.get(f"{B}/objects/region/REG-EC")
    assert region.status_code == 404
