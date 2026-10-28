"""Different fixture registries cannot rewrite the same append-only published seed baseline."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.modules.static.sections import BUILDERS
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.sections import isolated_sections, load_sections
from app.seeds.loader import run_seeds


async def test_static_then_full_seed_keeps_published_rows_immutable(users):
    # Committed QA/card baseline owners run in their own fresh databases, not this rollback-only group.
    assert not list(await users.scalars(select(OntologyRelease.id)))
    with isolated_sections(*BUILDERS):
        await run_seeds(users, ["versioning"])
    before = (
        (await users.execute(select(OntologyRelease.__table__).order_by(OntologyRelease.id))).mappings().all()
    )
    assert len(before) == 5
    with isolated_sections():
        load_sections()
        with pytest.raises(DBAPIError, match="ontology_release 只追加"):
            async with users.begin_nested():
                await run_seeds(users, ["versioning"])
    after = (
        (await users.execute(select(OntologyRelease.__table__).order_by(OntologyRelease.id))).mappings().all()
    )
    assert after == before
