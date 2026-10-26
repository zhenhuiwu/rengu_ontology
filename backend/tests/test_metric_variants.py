"""显式指标变体写回、历史隔离和回滚软引用保护。"""

import pytest
from sqlalchemy import select

from app.core import clock
from app.core.errors import AppError
from app.modules.static.explorer import metric_variant_index
from app.modules.static.models import ChangeRequest, CrItem, Metric, MetricStatus
from app.modules.static.repo import StaticRepo
from app.modules.static.sections import BUILDERS, MetricsSection
from app.modules.static.writeback import DIRTY, Ctx, metric_def, restore_metrics
from app.modules.versioning.sections import isolated_sections
from app.modules.versioning.service import VersioningService
from app.seeds.loader import run_seeds


@pytest.fixture
async def definitions(session):
    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await session.flush()
        yield session


def context(session):
    return Ctx(
        session, ChangeRequest(code="CR-TEST", owner="王敏"), "v2026.10.4", clock.now(), "U010001", False
    )


async def test_explicit_variants_write_snapshot_history_and_restore(definitions):
    s = definitions
    before = await MetricsSection().current(StaticRepo(s))
    variants = [{"code": "gmv_tax", "label": "新口径", "default": True, "api": "FAKE", "version": "v99"}]
    await metric_def(context(s), "gmv", [CrItem(item_key="variants", op="update", after=variants)])
    after = await MetricsSection().current(StaticRepo(s))
    target = await s.scalar(select(Metric).where(Metric.code == "gmv_tax"))
    row = after["gmv"]["variants"][0]
    assert row == {
        "code": "gmv_tax",
        "label": "新口径",
        "default": True,
        "api": target.api_name,
        "version": target.version,
    }
    assert "新口径" in await metric_variant_index(s)
    assert await metric_variant_index(s, "v2026.10.3") == {}, "旧快照缺显式变体不能猜测或借当前数据"
    s.info[DIRTY] = {"metrics"}
    ver = await VersioningService().create_version(s, ["CR-0008"], published_by="U010001", note="显式变体")
    target.status = MetricStatus.offline
    await s.flush()
    assert "新口径" not in await metric_variant_index(s)
    assert (await metric_variant_index(s, ver))["新口径"][0]["code"] == "gmv_tax"
    await restore_metrics(s, before, release_id=0)
    assert (await MetricsSection().current(StaticRepo(s)))["gmv"]["variants"] == before["gmv"]["variants"]
    await metric_def(context(s), "gmv", [CrItem(item_key="variants", op="delete", after={})])
    assert (await MetricsSection().current(StaticRepo(s)))["gmv"]["variants"] == []


@pytest.mark.parametrize(
    "value",
    [
        [{"code": "missing", "label": "口径", "default": True}],
        [{"code": "gmv", "label": "口径", "default": False}],
        [{"code": "gmv", "label": "口径", "default": True}] * 2,
    ],
)
async def test_invalid_variant_keeps_definition(definitions, value):
    s = definitions
    metric = await s.scalar(select(Metric).where(Metric.code == "gmv"))
    before = list(metric.variants)
    with pytest.raises(AppError) as error:
        await metric_def(context(s), "gmv", [CrItem(item_key="variants", op="update", after=value)])
    assert error.value.code == "WRITEBACK_FAILED"
    assert metric.variants == before


def new_metric(code):
    return Metric(
        code=code,
        api_name=code,
        display_name=code,
        metric_group="测试",
        unit="元",
        version="v1",
        status=MetricStatus.published,
        owner="测试",
        created_by="测试",
    )


@pytest.mark.parametrize("reference", ["catalog", "variant", "qa"])
async def test_rollback_keeps_referenced_metric_as_draft(definitions, reference):
    s = definitions
    snapshot = await MetricsSection().current(StaticRepo(s))
    kept, unused = new_metric("new_referenced"), new_metric("new_unused")
    s.add_all([kept, unused])
    if reference == "catalog":
        kept.ref_rows = [{"name": "已保存引用"}]
    elif reference == "variant":
        gmv = await s.scalar(select(Metric).where(Metric.code == "gmv"))
        gmv.variants = [{"code": kept.code, "label": "引用", "default": True}]
    else:
        from app.modules.qa.models import QaQueryLog

        s.add(
            QaQueryLog(
                question="实际已存问题",
                norm_question="实际已存问题",
                metric_code=kept.code,
                asked_at=clock.now(),
                kind="answer",
            )
        )
    await s.flush()
    await restore_metrics(s, snapshot, release_id=0)
    assert (await s.scalar(select(Metric).where(Metric.code == kept.code))).status == MetricStatus.draft
    assert await s.scalar(select(Metric).where(Metric.code == unused.code)) is None
