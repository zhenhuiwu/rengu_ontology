"""qa 种子：行数、§9.2 人员列改派 / 剔除、只追加表、重跑幂等。"""

import pytest
from sqlalchemy import text

from app.seeds.loader import run_seeds

EXPECTED = {
    "qa_session": 103,
    "qa_turn": 5,
    "qa_favorite": 18,
    "qa_session_option": 5,
    "qa_page": 32,
    "qa_page_card": 197,
    "qa_page_share_member": 16,
    "qa_page_subscription": 32,
    "qa_template": 22,
    "qa_template_card": 104,
    "qa_cert": 30,
    "qa_cert_variant": 10,
    "qa_eval_question": 40,
    "qa_eval_run": 21,
    "qa_eval_fail": 222,
    "qa_feedback": 26,
    "qa_freq_question": 32,
    "qa_synonym_link": 2,
    "qa_object_type_view": 4,
    "qa_turn_event": 0,
    "qa_permission_request": 0,
    "qa_query_log": 0,
}


@pytest.fixture
async def qa_seeded(users):
    await run_seeds(users, ["qa"])
    await users.flush()
    return users


async def _counts(session) -> dict[str, int]:
    return {t: (await session.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one() for t in EXPECTED}


async def test_qa_seed_counts_and_idempotent(qa_seeded):
    assert await _counts(qa_seeded) == EXPECTED
    await run_seeds(qa_seeded, ["qa"])  # 重跑：按业务键 upsert，只追加表的相同值 UPDATE 被触发器放行
    await qa_seeded.flush()
    assert await _counts(qa_seeded) == EXPECTED


async def test_qa_seed_people_columns(qa_seeded):
    owners = dict(
        (
            await qa_seeded.execute(
                text("SELECT p.code, u.code FROM qa_page p JOIN sys_user u ON u.id = p.owner_id")
            )
        ).all()
    )
    assert owners["P001"] == "U010001"
    assert [owners[c] for c in ("P901", "P902", "P903", "P904", "P905")] == [
        "U010231",
        "U010231",
        "U010231",
        "U010763",
        "U010006",  # 周婷 → 李娜 [推断] §9.2
    ]
    fb = (
        await qa_seeded.execute(
            text(
                "SELECT f.user_name, u.code FROM qa_feedback f JOIN sys_user u ON u.id = f.user_id"
                " ORDER BY f.code"
            )
        )
    ).all()
    assert sorted(fb) == sorted([("李娜", "U010006"), ("何静", "U010003"), ("冯雪", "U010910")])
    owners_s = (await qa_seeded.execute(text("SELECT DISTINCT owner_id FROM qa_session"))).scalars().all()
    assert len(owners_s) == 1
    # 种子不填 release_id
    for t in ("qa_turn", "qa_eval_run", "qa_object_type_view"):
        n = (
            await qa_seeded.execute(text(f"SELECT count(*) FROM {t} WHERE release_id IS NOT NULL"))
        ).scalar_one()
        assert n == 0, t


async def test_qa_append_only_tables_reject_update(qa_seeded):
    await qa_seeded.flush()
    with pytest.raises(Exception, match="只追加"):
        async with qa_seeded.begin_nested():
            await qa_seeded.execute(text("UPDATE qa_eval_fail SET actual = 'x'"))
