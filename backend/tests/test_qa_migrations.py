"""qa 迁移：模块往返（骨架 helper，CONVENTIONS §16）、release_id 外键（qa_0002，§6）。"""

from itertools import pairwise

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.db.models  # noqa: F401  加载全部模型
from app.db.base import Base
from app.seeds.loader import run_seeds
from tests.helpers.migrations import assert_module_round_trip, module_parent, module_revisions

# qa 自己的表：按模型所在模块取（不按表名前缀猜）
QA_TABLES = {
    m.local_table.name for m in Base.registry.mappers if m.class_.__module__ == "app.modules.qa.models"
}
RELEASE_FK_TABLES = ("qa_eval_run", "qa_object_type_view", "qa_turn")


def test_qa_tables_listed():
    assert len(QA_TABLES) == 22 and all(t.startswith("qa_") for t in QA_TABLES)


def test_qa_revisions_in_chain_order():
    """只断言不随合并变化的性质：qa 的迁移在链上按编号先后出现、第一个的父不属于 qa。

    qa_0002 接的是合并时的 head（别的模块的迁移可以夹在中间），所以不要求紧挨着。
    """
    revs = [s.revision for s in module_revisions("qa")]
    assert revs[:2] == ["qa_0001", "qa_0002"]
    assert not module_parent("qa").startswith("qa_")
    for prev, cur in pairwise(revs):
        assert int(prev.split("_")[1]) < int(cur.split("_")[1])


async def test_qa_migration_round_trip():
    await assert_module_round_trip("qa", QA_TABLES)


async def test_release_fk_no_action(session):
    rows = (
        await session.execute(
            text(
                "SELECT c.conrelid::regclass::text, c.confdeltype::text, c.confrelid::regclass::text"
                " FROM pg_constraint c WHERE c.contype = 'f' AND c.conname LIKE 'fk_qa_%_release_id_%'"
            )
        )
    ).all()
    assert sorted(rows) == [(t, "a", "ontology_release") for t in RELEASE_FK_TABLES]  # a = NO ACTION


async def _copy_view_row(session, release_id: int) -> None:
    """qa_object_type_view 只追加（不许 UPDATE）：复制一行、换 release_id 插入。"""
    cols = [
        c
        for c in (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'qa_object_type_view' AND column_name NOT IN ('id', 'release_id')"
                )
            )
        ).scalars()
    ]
    cl = ", ".join(cols)
    await session.execute(
        text(
            f"INSERT INTO qa_object_type_view ({cl}, release_id)"
            f" SELECT {cl}, :r FROM qa_object_type_view ORDER BY id LIMIT 1"
        ),
        {"r": release_id},
    )


async def test_release_fk_enforced(seeded):
    await run_seeds(seeded, ["qa"])
    await seeded.flush()
    rid = await seeded.scalar(text("SELECT max(id) FROM ontology_release"))
    for t in ("qa_eval_run", "qa_turn"):
        sql = f"UPDATE {t} SET release_id = :r WHERE id = (SELECT min(id) FROM {t})"
        sp = await seeded.begin_nested()
        with pytest.raises(IntegrityError) as fk:
            await seeded.execute(text(sql), {"r": rid + 1000})
        assert fk.value.orig.sqlstate == "23503"
        await sp.rollback()
        await seeded.execute(text(sql), {"r": rid})  # 指向真实版本可以
    sp = await seeded.begin_nested()
    with pytest.raises(IntegrityError) as fk:
        await _copy_view_row(seeded, rid + 1000)
    assert fk.value.orig.sqlstate == "23503"
    await sp.rollback()
    await _copy_view_row(seeded, rid)
