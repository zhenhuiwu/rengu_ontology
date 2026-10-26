"""自然键足够支持路径重复导入，移除展示分组的重复唯一约束不破坏关联。"""

from sqlalchemy import func, select, text

from app.modules.static.models import QaPath, QaPathStep
from app.modules.static.sections import BUILDERS
from app.modules.versioning.sections import isolated_sections
from app.seeds.loader import run_seeds


async def test_natural_path_key_preserves_seed_upsert_and_references(session):
    names = set(
        await session.scalars(
            text("SELECT conname FROM pg_constraint WHERE conrelid='onto_qa_path'::regclass AND contype='u'")
        )
    )
    assert "uq_onto_qa_path_from_object_id_to_object_id_seq" in names
    assert "uq_onto_qa_path_group_key_seq" not in names
    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        before = await session.scalar(select(func.count()).select_from(QaPath))
        first_ids = set(await session.scalars(select(QaPath.id)))
        await run_seeds(session, ["admin", "versioning", "static"])
    assert await session.scalar(select(func.count()).select_from(QaPath)) == before > 0
    assert set(await session.scalars(select(QaPath.id))) == first_ids
    step_paths = set(await session.scalars(select(QaPathStep.path_id)))
    assert step_paths and step_paths <= first_ids
