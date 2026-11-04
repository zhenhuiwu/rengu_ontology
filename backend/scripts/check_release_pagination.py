"""Formal release pagination SQL/route contract checks, without database or HTTP."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.dialects import postgresql

from app.common.pagination import PageParams
from app.modules.versioning.models import ReleaseKind
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.router import list_releases, router


def release(ver):
    return SimpleNamespace(
        ver=ver,
        scope="formal",
        published_at=datetime(2026, 11, 4, tzinfo=UTC),
        published_by="Publisher",
        published_by_code="U1",
        note="Formal release",
        parent_ver=None,
        kind=ReleaseKind.publish,
        rollback_of=None,
        change_order_ids=[],
    )


def sql(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), compiled.params


class Session:
    def __init__(self, total):
        self.total = total
        self.counts, self.pages = [], []

    async def scalar(self, statement):
        self.counts.append(statement)
        return self.total

    async def scalars(self, statement):
        self.pages.append(statement)
        return SimpleNamespace(all=lambda: [])


async def main():
    session = Session(25)
    rows, total = await ReleaseRepo(session).formal_page(PageParams(page=99, size=10), "  A%_  ")
    assert rows == [] and total == 25
    count, count_params = sql(session.counts[0])
    page, page_params = sql(session.pages[0])
    assert "count(*)" in count and "ontology_release.scope =" in count
    assert count_params["scope_1"] == page_params["scope_1"] == "formal"
    assert "ontology_release.ver ILIKE" in page and "ontology_release.note ILIKE" in page
    assert "A/%/_" in page_params.values(), "search wildcards must be escaped as literal text"
    assert page_params["param_1"] == 10 and page_params["param_2"] == 20
    assert "ORDER BY ontology_release.id DESC" in page
    assert "snapshot," not in page and ".snapshot " not in page and "change_summary" not in page
    assert "ontology_release_1.ver" in page and "ontology_release_2.ver" in page
    assert len(session.counts) == len(session.pages) == 1
    empty = Session(0)
    await ReleaseRepo(empty).formal_page(PageParams(page=99, size=10))
    assert sql(empty.pages[0])[1]["param_2"] == 0
    assert "ILIKE" not in sql(empty.pages[0])[0]
    latest = Session("v2026.11.9")
    assert await ReleaseRepo(latest).latest_formal_ver() == "v2026.11.9"
    latest_sql, latest_params = sql(latest.counts[0])
    assert "scope" in latest_sql and latest_params["scope_1"] == "formal"
    assert "note" not in latest_sql and "snapshot" not in latest_sql
    # Defer applies to the new page query alone, not snapshots or existing callers.
    legacy = Session(0)
    await ReleaseRepo(legacy).formal()
    await ReleaseRepo(legacy).by_ver("v2026.11.1")
    assert "ontology_release.snapshot" in sql(legacy.pages[0])[0]
    assert "ontology_release.snapshot" in sql(legacy.counts[0])[0]

    catalog = [release("v2026.11.9"), release("v2026.11.8")]
    repo = SimpleNamespace(
        formal=AsyncMock(return_value=catalog),
        formal_page=AsyncMock(return_value=([catalog[1]], 25)),
        latest_formal_ver=AsyncMock(return_value=catalog[0].ver),
    )
    with patch("app.modules.versioning.router.ReleaseRepo", return_value=repo):
        full = await list_releases(None, None)
        assert set(full.model_dump(exclude_unset=True)) == {"items", "current"}
        assert len(full.items) == 2 and full.current == "v2026.11.9"
        repo.formal_page.assert_not_awaited()
        paged = await list_releases(None, None, page=99, size=10, kw="older")
        assert paged.current == "v2026.11.9" and paged.items[0].ver == "v2026.11.8"
        assert (paged.total, paged.page, paged.size) == (25, 3, 10)
        assert repo.formal_page.await_args.args[1] == "older"
        # Supplying only search or only size also opts into paging, preserving the unfiltered current.
        await list_releases(None, None, kw="older")
        assert repo.formal_page.await_args.args[0] == PageParams(page=1, size=20)
        await list_releases(None, None, size=5)
        assert repo.formal_page.await_args.args[0] == PageParams(page=1, size=5)
        repo.formal_page.return_value = ([], 0)
        empty_response = await list_releases(None, None, page=-1, size=10, kw="absent")
        assert (empty_response.page, empty_response.total, empty_response.current) == (1, 0, "v2026.11.9")
        assert repo.formal.await_count == 1
    route = next(r for r in router.routes if r.path == "/releases" and "GET" in r.methods)
    assert route.response_model_exclude_unset
    assert any(d.call.__name__ == "get_current_user" for d in route.dependant.dependencies)
    fields = {field.name: field for field in route.dependant.query_params}
    for invalid_size in ("0", "201"):
        assert fields["size"].validate(invalid_size, {}, loc=("query", "size"))[1]
    assert fields["kw"].validate("bad\x00", {}, loc=("query", "kw"))[1]
    print(
        "release pagination: PASS (formal SQL, clamping, search, stable current, legacy contract; no DB/HTTP)"
    )


if __name__ == "__main__":
    asyncio.run(main())
