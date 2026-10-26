"""总览聚合：真实表、历史快照、待处理分页和登录边界。"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from app.core import clock
from app.modules.admin.models import SysUser
from app.modules.event.models import Event, EventOutcome
from app.modules.static.models import ChangeRequest, CRStatus, Metric, MetricStatus
from app.modules.versioning.models import OntologyRelease
from app.seeds.loader import run_seeds
from tests.test_static import static_sections, static_seeded  # noqa: F401

ROOT = "/api/ontology/v1/ontology"


async def test_quality_history_uses_published_membership_and_run_cutoff(overview_db):
    from app.common.auth import CurrentUser
    from app.common.context import ViewContext
    from app.modules.data.quality_models import QualityCheck, QualityRun
    from app.modules.static.models import ObjectType
    from app.modules.static.overview import quality_state
    from app.modules.static.service import StaticService

    s = overview_db
    obj = await s.scalar(select(ObjectType).where(ObjectType.code == "store"))
    cutoff = clock.now()
    check = QualityCheck(
        code="QC-HIST",
        object_type_id=obj.id,
        name="历史检查",
        kind="空值率",
        threshold="≤ 0%",
        limit_value=0,
        schedule="手动",
        created_by="U010001",
    )
    s.add(check)
    await s.flush()
    for offset, result in [(-1, "失败"), (1, "通过")]:
        s.add(
            QualityRun(
                check_id=check.id,
                job_id=f"history-{offset}",
                time=cutoff + timedelta(minutes=offset),
                result=result,
                actual="实际结果",
                ms=1,
                data_cutoff=cutoff,
                definition={},
            )
        )
    release = OntologyRelease(
        ver="v2030.01.1",
        snapshot={"qualityChecks": {check.code: {"ownerId": "U010001", "retired": False}}},
        snapshot_sha256="0" * 64,
        published_at=cutoff,
        published_by_id=await s.scalar(select(SysUser.id).where(SysUser.code == "U010001")),
    )
    s.add(release)
    check.retired_at = cutoff + timedelta(minutes=2)
    await s.flush()
    user = CurrentUser(id="U010001", name="实际用户")
    checks, runs = await quality_state(StaticService(s, ViewContext(ver=release.ver)), user)
    assert [row.code for row in checks] == [check.code]
    assert runs[check.id].result == "失败", "历史不能借用发布后才产生的成功记录"
    assert await quality_state(StaticService(s, ViewContext()), user) == ([], {})
    assert await quality_state(StaticService(s, ViewContext(ver="v2026.10.3")), user) == ([], {})


@pytest.fixture
async def overview_db(static_seeded):  # noqa: F811
    await run_seeds(static_seeded, ["integration", "action", "event"])
    await static_seeded.commit()
    return static_seeded


async def test_overview_uses_tables_and_no_fake_quality(client, overview_db):
    response = await client.get(f"{ROOT}/overview")
    assert response.status_code == 200, response.text
    body = response.json()
    cards = {c["key"]: c for c in body["cards"]}
    assert set(cards) == {"obj", "link", "action", "event", "metric", "dim"}
    assert body["qualityConfigured"] is False
    assert not any("质量检查失败" in h["text"] for c in cards.values() for h in c["health"])
    count = await overview_db.scalar(
        select(func.count()).select_from(Metric).where(Metric.status == MetricStatus.published)
    )
    assert cards["metric"]["count"] == count
    failed = await overview_db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.outcome == EventOutcome.failed, Event.is_test.is_(False))
    )
    assert cards["event"]["health"][1]["n"] == failed
    metric = await overview_db.scalar(select(Metric).where(Metric.status == MetricStatus.published).limit(1))
    await overview_db.execute(update(Metric).where(Metric.id == metric.id).values(status=MetricStatus.draft))
    await overview_db.commit()
    after = (await client.get(f"{ROOT}/overview")).json()
    assert next(c for c in after["cards"] if c["key"] == "metric")["count"] == count - 1


async def test_overview_history_and_todos(client, overview_db):
    release = OntologyRelease(
        ver="v2020.01.1",
        snapshot={
            "objectTypes": {
                "historical": {
                    "id": "historical",
                    "name": "历史对象",
                    "api": "Historical",
                    "group": "历史",
                    "status": "已发布",
                }
            },
            "metrics": {"historical": {"id": "historical", "status": "已发布"}},
            "links": {},
            "dimensions": {},
            "actionTypes": {},
            "eventTypes": {},
        },
        snapshot_sha256="0" * 64,
        published_by_id=await overview_db.scalar(select(SysUser.id).limit(1)),
        published_at=clock.now(),
    )
    overview_db.add(release)
    await overview_db.commit()
    body = (await client.get(f"{ROOT}/overview", params={"ver": release.ver})).json()
    assert {c["key"]: c["count"] for c in body["cards"]} == {
        "obj": 1,
        "metric": 1,
        "link": 0,
        "dim": 0,
        "action": 0,
        "event": 0,
    }
    graph = (await client.get(f"{ROOT}/graph", params={"ver": release.ver})).json()
    assert [n["id"] for n in graph["nodes"]] == ["historical"]
    assert (await client.get(f"{ROOT}/todos", params={"ver": release.ver})).json()["total"] == 0
    count = await overview_db.scalar(
        select(func.count())
        .select_from(ChangeRequest)
        .where(ChangeRequest.status.in_([CRStatus.draft, CRStatus.in_review, CRStatus.approved]))
    )
    todos = (await client.get(f"{ROOT}/todos", params={"page": 999, "size": 2})).json()
    assert todos["total"] == count
    assert todos["page"] == max(1, (count + 1) // 2)
    assert {t["kind"] for t in todos["items"]} <= {"变更"}
    quality = (await client.get(f"{ROOT}/todos", params={"kind": "数据质量"})).json()
    assert quality["items"] == [] and quality["qualityConfigured"] is False


async def test_overview_read_boundaries(client, overview_db):
    assert (await client.get(f"{ROOT}/overview", headers={"X-Demo-As": "unknown"})).status_code == 401
    assert (await client.get(f"{ROOT}/overview", params={"ver": "v1900.01.1"})).status_code == 404
    assert (await client.get(f"{ROOT}/todos", params={"kind": "invalid"})).status_code == 422
    assert (await client.get(f"{ROOT}/overview", params={"previewAs": "U010006"})).status_code == 200


async def test_overview_quality_latest_runs_and_owner(client, overview_db):
    from app.modules.data.quality_models import QualityCheck, QualityRun
    from app.modules.static.models import ObjectType

    obj_id = await overview_db.scalar(select(ObjectType.id).where(ObjectType.code == "store"))
    checks = [
        QualityCheck(
            code=code,
            name=code,
            object_type_id=obj_id,
            kind="唯一性",
            threshold="重复0条",
            limit_value=0,
            schedule="手动",
            created_by=owner,
        )
        for code, owner in [("QC-OWN", "U010001"), ("QC-UNRUN", "U010001"), ("QC-OTHER", "U010006")]
    ]
    overview_db.add_all(checks)
    await overview_db.flush()

    def result(check, status):
        return QualityRun(
            check_id=check.id,
            job_id="test-job",
            time=clock.now(),
            result=status,
            actual="重复1条" if status == "失败" else "重复0条",
            ms=1,
            data_cutoff=clock.now(),
            definition={},
            created_by=check.created_by,
        )

    overview_db.add_all([result(checks[0], "失败"), result(checks[2], "失败")])
    await overview_db.commit()
    body = (await client.get(f"{ROOT}/overview")).json()
    assert body["qualityConfigured"] is True
    health = next(c for c in body["cards"] if c["key"] == "obj")["health"]
    assert next(h for h in health if h["text"] == "项质量检查失败")["n"] == 1
    assert next(h for h in health if h["text"] == "项质量检查未运行 / 无数据")["n"] == 1
    own = (await client.get(f"{ROOT}/todos", params={"kind": "数据质量"})).json()
    assert [r["id"] for r in own["items"]] == ["QC-OWN"]
    other = (await client.get(f"{ROOT}/todos", params={"kind": "数据质量", "previewAs": "U010006"})).json()
    assert [r["id"] for r in other["items"]] == ["QC-OTHER"]
    overview_db.add(result(checks[0], "通过"))
    await overview_db.commit()
    after = (await client.get(f"{ROOT}/todos", params={"kind": "数据质量"})).json()
    assert after["total"] == 0, "a successful latest run must clear the old failed todo"
