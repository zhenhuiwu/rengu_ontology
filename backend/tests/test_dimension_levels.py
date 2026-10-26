"""层级顺序写回保留真实统计，层级被取值引用时不可直接删除。"""

from copy import deepcopy

import pytest
from sqlalchemy import select

from app.core import clock
from app.core.errors import AppError
from app.modules.static.models import ChangeRequest, CrItem, Dimension, DimValue, DimValueStatus
from app.modules.static.repo import StaticRepo
from app.modules.static.sections import BUILDERS, DimensionsSection, ObjectTypesSection
from app.modules.static.writeback import HOOKS, Ctx, restore_dimensions
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.sections import isolated_sections, restore_sections
from app.seeds.loader import run_seeds


@pytest.fixture
async def definitions(session):
    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await session.flush()
        yield session


async def save_levels(s, rows):
    ctx = Ctx(s, ChangeRequest(code="CR-LEVEL", owner="测试"), "v2026.10.4", clock.now(), "测试", False)
    return await HOOKS["dimension", "def"](ctx, "time", [CrItem(item_key="levels", op="update", after=rows)])


async def test_levels_reorder_add_remove_and_restore_preserve_counts(definitions):
    s = definitions
    dimension = await s.scalar(select(Dimension).where(Dimension.code == "time"))
    before = await DimensionsSection().current(StaticRepo(s))
    ids = {level.api_name: (level.id, level.value_count) for level in dimension.levels}
    rows = [{**row, "count": 999999, "note": "修改说明"} for row in reversed(before["time"]["levels"])]
    rows.append({"name": "新层级", "api": "new_level", "source": "实际来源", "note": "新说明"})
    await save_levels(s, rows)
    assert [level.api_name for level in dimension.levels] == [row["api"] for row in rows]
    assert all((level.id, level.value_count) == ids[level.api_name] for level in dimension.levels[:-1])
    assert dimension.levels[-1].value_count == 0 and dimension.levels[-1].note == "新说明"
    await save_levels(s, rows[:-1])
    assert all(level.api_name != "new_level" for level in dimension.levels)
    release = await s.scalar(select(OntologyRelease).where(OntologyRelease.ver == "v2026.10.3"))
    await restore_dimensions(s, before, release_id=release.id)
    assert [level.api_name for level in dimension.levels] == [row["api"] for row in before["time"]["levels"]]
    assert all((level.id, level.value_count) == ids[level.api_name] for level in dimension.levels)


async def test_used_level_cannot_be_deleted(definitions):
    s = definitions
    dimension = await s.scalar(select(Dimension).where(Dimension.code == "time"))
    before = [(level.id, level.level_no, level.name) for level in dimension.levels]
    with pytest.raises(AppError) as error:
        await save_levels(s, [])
    assert error.value.code == "WRITEBACK_FAILED"
    assert [(level.id, level.level_no, level.name) for level in dimension.levels] == before


async def test_complete_restore_uses_target_levels_and_values_and_keeps_archived_rows(definitions):
    session = definitions
    dimension = await session.scalar(select(Dimension).where(Dimension.code == "time"))
    target = await DimensionsSection().current(StaticRepo(session))
    desired = target["time"]["levels"]
    used = target["time"]["values"][0]["level"]
    next(row for row in desired if row["name"] == used)["api"] = "historical_used_level"
    current = [
        {"name": level.name, "api": level.api_name, "source": level.source} for level in dimension.levels
    ]
    await save_levels(
        session, [*current, {"name": "将被回滚的层级", "api": "forward_level", "source": "测试"}]
    )
    forward = DimValue(
        dimension_id=dimension.id,
        value_key="forward:V",
        code="V",
        name="保留历史取值",
        level_name="将被回滚的层级",
        status=DimValueStatus.enabled,
        synonyms=[],
    )
    session.add(forward)
    await session.flush()
    forward_id = forward.id
    release = await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == "v2026.10.3"))
    await restore_dimensions(session, target, release_id=release.id)
    assert [level.api_name for level in dimension.levels] == [row["api"] for row in desired]
    values = list(await session.scalars(select(DimValue).where(DimValue.dimension_id == dimension.id)))
    archived = next(row for row in values if row.id == forward_id)
    assert archived.value_key == "forward:V" and archived.until_release == release.ver
    live = [row for row in values if row.until_release is None]
    assert {row.value_key for row in live} == {row["key"] for row in target["time"]["values"]}
    assert all(row.level_name in {level.name for level in dimension.levels} for row in live)


async def test_invalid_historical_snapshot_rejects_without_partial_restore(definitions):
    session = definitions
    before = await DimensionsSection().current(StaticRepo(session))
    objects = await ObjectTypesSection().current(StaticRepo(session))
    old = await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == "v2026.09.3"))
    current = await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == "v2026.10.3"))
    snapshot = deepcopy(old.snapshot)
    # The original historical org snapshot contains active 大区 values under only 省份/门店 levels.
    snapshot["objectTypes"]["order"]["name"] = "不得留下的中途写回"
    with pytest.raises(AppError) as error:
        await restore_sections(session, snapshot, current.id)
    assert error.value.code == "WRITEBACK_FAILED" and error.value.details["id"] == "org"
    assert "不存在的层级" in error.value.message
    assert await DimensionsSection().current(StaticRepo(session)) == before
    assert await ObjectTypesSection().current(StaticRepo(session)) == objects
