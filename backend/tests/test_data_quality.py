"""质量检查只记录真实运行；无演示历史、无任意SQL、身份隔离。"""

import json
from datetime import timedelta

import pytest
from sqlalchemy import false, select, update

from app.core import clock
from app.modules.biz.models import BizOrder
from app.modules.data import quality
from app.modules.data.quality_models import QualityCheck, QualityRun
from app.seeds.loader import run_seeds
from tests.test_static import static_sections, static_seeded  # noqa: F401

API = "/api/data/v1/quality-checks"


@pytest.fixture
async def quality_db(static_seeded):  # noqa: F811
    await run_seeds(static_seeded, ["biz"])
    await static_seeded.commit()
    return static_seeded


async def create(client, session, kind="新鲜度", threshold="≤ 1 小时", type_key="order"):
    response = await client.post(
        API,
        json={
            "name": "真实检查",
            "typeKey": type_key,
            "kind": kind,
            "threshold": threshold,
            "schedule": "手动",
        },
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    # Existing evaluator tests publish the staged definition through the real writeback hook.
    from app.modules.static.models import ChangeRequest, CRStatus
    from app.modules.static.writeback import apply_crs

    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == draft["crId"]))
    await apply_crs(session, [cr], "v2026.10.4", clock.now(), "U010001")
    cr.status = CRStatus.published
    await session.flush()
    return next(row for row in (await client.get(API)).json()["items"] if row["id"] == draft["id"])


async def test_empty_create_real_runs_and_snapshot(client, quality_db, monkeypatch):
    empty = (await client.get(API)).json()
    assert empty["items"] == [] and empty["summary"]["passed"] == 0 and empty["dataCutoff"] == ""
    new = await create(client, quality_db)
    assert new["result"] == "未运行" and new["history"] == []
    assert (await client.get(f"{API}/{new['id']}/runs")).json()["total"] == 0
    id_ = await quality_db.scalar(select(BizOrder.id).limit(1))

    async def only_one(stmt, *_args):
        return stmt.where(BizOrder.id == id_)

    monkeypatch.setattr(quality, "apply_row_filter", only_one)
    await quality_db.execute(
        update(BizOrder).where(BizOrder.id == id_).values(created_at_biz=clock.now() - timedelta(hours=3))
    )
    response = await client.post(f"{API}/{new['id']}:run")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["completed"] and body["run"]["result"] == "失败"
    assert "3.00 小时" in body["run"]["actual"]
    await quality_db.execute(
        update(BizOrder).where(BizOrder.id == id_).values(created_at_biz=clock.now() - timedelta(minutes=30))
    )
    second = (await client.post(f"{API}/{new['id']}:run")).json()
    assert second["run"]["result"] == "通过"
    check = await quality_db.scalar(select(QualityCheck))
    check.name = "后来修改的定义"
    await quality_db.flush()
    history = (await client.get(f"{API}/{new['id']}/runs", params={"size": 1, "page": 2})).json()
    assert history["total"] == 2 and history["items"][0]["definition"]["name"] == "真实检查"
    assert history["items"][0]["sourceTime"] != history["items"][0]["time"]
    assert (await quality_db.scalar(select(QualityRun).limit(1))).job_id == body["jobId"]


async def test_all_defined_types_and_batch_are_real(client, quality_db):
    for kind, threshold in [("空值率", "≤ 0%"), ("唯一性", "重复 0 条"), ("引用完整性", "找不到 0 条")]:
        new = await create(client, quality_db, kind, threshold)
        response = await client.post(f"{API}/{new['id']}:run")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        assert run["definition"]["kind"] == kind and run["result"] in {"通过", "失败"}
    result = (await client.post(f"{API}:run-all")).json()
    assert result["completed"] and result["count"] == 3
    checks = (await client.get(API)).json()
    assert checks["total"] == 3 and checks["summary"]["unrun"] == 0
    runs = (await quality_db.scalars(select(QualityRun).where(QualityRun.job_id == result["jobId"]))).all()
    assert len(runs) == 3


async def test_identity_preview_readonly_and_unsupported(client, quality_db):
    new = await create(client, quality_db)
    viewer = {"X-Demo-As": "U010006"}
    assert (await client.get(API, headers=viewer)).status_code == 403
    assert (await client.post(f"{API}/{new['id']}:run", headers=viewer)).status_code == 403
    assert (await client.get(API, params={"previewAs": "U010006"})).status_code == 403
    assert (await client.post(f"{API}/{new['id']}:run?ver=v2026.10.3")).status_code == 403
    bad = await client.post(
        API, json={"name": "不可用", "typeKey": "refund", "kind": "新鲜度", "threshold": "≤ 1 小时"}
    )
    assert bad.status_code == 422 and bad.json()["code"] == "QUALITY_NOT_CONFIGURED"
    bad = await client.post(
        API, json={"name": "拒SQL", "typeKey": "order", "kind": "新鲜度", "threshold": "SELECT * FROM store"}
    )
    assert bad.status_code == 422
    assert (await client.get(f"{API}/x%00/runs")).status_code == 422


async def test_no_visible_data_is_not_healthy(client, quality_db, monkeypatch):
    new = await create(client, quality_db)

    async def deny(stmt, *_args):
        return stmt.where(false())

    monkeypatch.setattr(quality, "apply_row_filter", deny)
    result = (await client.post(f"{API}/{new['id']}:run")).json()["run"]
    assert result["result"] == "无数据"
    assert (await client.get(API)).json()["summary"]["passed"] == 0


async def test_all_supported_object_mappings(client, quality_db):
    objects = {o["key"]: o for o in (await client.get(f"{API}/meta")).json()["objects"]}
    assert objects["refund"]["kinds"] == []
    assert objects["store"]["kinds"] == ["空值率", "唯一性"]
    for code, kind, threshold in [
        ("order_line", "空值率", "≤ 0%"),
        ("order_line", "唯一性", "重复 0 条"),
        ("order_line", "引用完整性", "找不到 0 条"),
        ("store", "空值率", "≤ 0%"),
        ("store", "唯一性", "重复 0 条"),
        ("product", "空值率", "≤ 0%"),
        ("product", "唯一性", "重复 0 条"),
    ]:
        check = await create(client, quality_db, kind, threshold, code)
        response = await client.post(f"{API}/{check['id']}:run")
        assert response.status_code == 200, response.text
        assert response.json()["run"]["result"] in {"通过", "失败"}


@pytest.mark.parametrize("field", ["name", "typeKey", "threshold"])
@pytest.mark.parametrize("text", ["bad\x00value", "bad\ud800value"])
async def test_quality_text_rejected_before_database(client, users, field, text):
    from tests.function_access_support import grant_fixture_roles

    await grant_fixture_roles(users)
    body = {"name": "check", "typeKey": "order", "kind": "新鲜度", "threshold": "≤ 1 小时"}
    body[field] = text
    response = await client.post(
        API, content=json.dumps(body, ensure_ascii=True), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_FAILED"
    assert [f["field"] for f in response.json()["details"]["fields"]] == [field]
