"""知识试用仅返回真实定义匹配，遵守保存草稿及历史边界，不执行数据查询。"""

from datetime import datetime

from sqlalchemy import func, select

from app.core import clock
from app.modules.static.knowledge_trial import preview_rows
from app.modules.static.models import ChangeRequest, CrItem, CRLevel, CRStatus, CrTarget, Knowledge
from app.modules.static.sections import BUILDERS
from app.modules.versioning.sections import isolated_sections
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

P = "/api/ontology/v1/knowledge:resolve"


def test_real_words_conflicts_calendar_and_clock():
    terms = [
        {
            "id": "actual-knowledge",
            "name": "实际术语",
            "type": "术语",
            "synonyms": ["共享说法"],
            "filters": [{"field": "订单.状态", "cond": "=有效"}],
            "applies": [{"kind": "metric", "id": "actual-metric"}],
        },
        {"id": "other-knowledge", "name": "另一术语", "type": "术语", "synonyms": ["共享说法"]},
    ]
    metrics = [{"id": "actual-metric", "name": "真实指标名", "version": "v42"}]
    with clock.frozen(datetime.fromisoformat("2030-02-12T10:00:00+08:00")):
        rows = preview_rows(
            "昨天各测试维度实际术语", "actual-knowledge", terms, metrics, [{"levels": [{"name": "测试维度"}]}]
        )
    by_key = {r["k"]: r["v"] for r in rows}
    assert by_key["时间"] == "2030-02-11"
    assert by_key["指标"] == "真实指标名 v42"
    assert by_key["分组"] == "按测试维度"
    assert by_key["默认过滤"] == "订单.状态 =有效"
    rows = preview_rows("共享说法", "actual-knowledge", terms, metrics, [])
    assert "同义词冲突" in rows[0]["v"]
    assert next(r for r in rows if r["k"] == "默认过滤")["v"] == "无"
    terms[0]["periods"] = [{"name": "真实活动区间", "start": "2030-03-02", "end": "2030-03-07"}]
    rows = preview_rows("真实活动区间", "actual-knowledge", terms, metrics, [])
    assert next(r for r in rows if r["k"] == "时间")["v"] == "2030-03-02 至 2030-03-07（真实活动区间）"


async def test_saved_cr_overlay_isolated_and_readonly_http(client, session):
    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
    await grant_fixture_roles(session)
    knowledge = await session.scalar(select(Knowledge).where(Knowledge.code == "K-001"))
    original = list(knowledge.synonyms)
    cr = ChangeRequest(
        code="CR-9905",
        title="知识真实试用",
        owner="王敏",
        reviewer="",
        status=CRStatus.draft,
        level=CRLevel.minor,
        status_at=clock.now(),
        created_by="U010001",
        detail={},
    )
    session.add(cr)
    await session.flush()
    session.add_all(
        [
            CrTarget(cr_id=cr.id, target_kind="knowledge", target_id=knowledge.code),
            CrItem(
                cr_id=cr.id,
                target_kind="knowledge",
                target_id=knowledge.code,
                section="content",
                item_key="synonyms",
                op="update",
                before=original,
                after=["真实独有词"],
            ),
            CrItem(
                cr_id=cr.id,
                target_kind="knowledge",
                target_id="K-002",
                section="content",
                item_key="synonyms",
                op="update",
                before=[],
                after=["另一目标独有词"],
            ),
        ]
    )
    await session.flush()
    count = await session.scalar(select(func.count()).select_from(CrItem))
    body = {"text": "真实独有词", "knowledge": knowledge.code}
    response = await client.post(P, json=body)
    assert response.status_code == 200, response.text
    assert not any(r.get("by") == knowledge.code for r in response.json()["rows"])
    response = await client.post(P, json={**body, "crId": cr.code})
    assert response.status_code == 200, response.text
    assert any(r.get("by") == knowledge.code for r in response.json()["rows"])
    response = await client.post(P, json={**body, "text": "另一目标独有词", "crId": cr.code})
    assert response.status_code == 200 and not any(r.get("by") for r in response.json()["rows"])
    response = await client.post(P + "?ver=v2026.10.3", json=body)
    assert response.status_code == 200, response.text
    response = await client.post(P + "?ver=v2026.10.3", json={**body, "crId": cr.code})
    assert response.status_code == 422
    response = await client.post(P + "?previewAs=U010006", json=body)
    assert response.status_code == 403
    response = await client.post(P, json={**body, "text": " "})
    assert response.status_code == 409 and response.json()["code"] == "EMPTY_INPUT"
    response = await client.post(P, json={**body, "text": "\x00"})
    assert response.status_code == 422
    assert await session.scalar(select(func.count()).select_from(CrItem)) == count
    assert knowledge.synonyms == original
