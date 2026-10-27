"""contracts/static.semantic_map（static-api.md §3.7）：进程内本体词表，读快照，不开 HTTP。

运行：pytest tests/test_static_semantic_map.py（真实 PostgreSQL）
"""

import pytest
from sqlalchemy import select

from app.contracts.static import semantic_map
from app.core.errors import NotFound
from app.modules.static.semantic_map import KINDS, entries_from_snapshot
from app.modules.versioning.models import OntologyRelease
from app.seeds.loader import run_seeds


@pytest.fixture
async def seeded(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await session.commit()
        yield session


def find(rows, kind, id_):
    return next(x for x in rows if x["kind"] == kind and x["id"] == id_)


async def test_current_version_vocabulary(seeded):
    rows = await semantic_map(seeded)
    assert {x["kind"] for x in rows} == set(KINDS)
    assert all(set(x) == {"kind", "id", "api", "parent", "name", "synonyms"} for x in rows)
    assert find(rows, "object", "order")["api"] == "SalesOrder"
    prop = find(rows, "property", "order_id")
    assert prop["parent"] == "SalesOrder" and prop["synonyms"] == []
    assert find(rows, "metric", "gmv")["api"] == "sales_amount_net"
    dim = find(rows, "dimension", "org")
    assert "华东" in dim["synonyms"] and dim["parent"] is None
    # 说法「华东」→ 取值「华东大区」：词并入取值，parent 是维度 api_name，api 是取值 code
    value = next(x for x in rows if x["kind"] == "dimValue" and x["name"] == "华东大区")
    assert value["parent"] == "dim_org" and "华东" in value["synonyms"]
    term = find(rows, "term", "K-001")
    assert term["api"] == "K-001" and "回头客" in term["synonyms"]


async def test_ver_reads_that_snapshot_and_unknown_404(seeded):
    vers = (await seeded.scalars(select(OntologyRelease.ver).order_by(OntologyRelease.ver))).all()
    oldest = await semantic_map(seeded, vers[0])
    assert oldest and len(oldest) <= len(await semantic_map(seeded))
    with pytest.raises(NotFound):
        await semantic_map(seeded, "v1999.01.1")


def test_filters_drafts_and_stopped_terms():
    snap = {
        "objectTypes": {"a": {"api": "A", "name": "甲", "status": "草稿"}, "b": {"api": "B", "name": "乙"}},
        "properties": {"a": {"x": {"api": "x", "name": "x"}}, "b": {"y": {"api": "y", "name": "y"}}},
        "metrics": {"m": {"api": "m", "name": "m", "status": "草稿"}, "n": {"api": "n", "name": "n"}},
        "terms": {
            "K-1": {"name": "停", "status": "已停用"},
            "K-2": {"name": "用", "synonyms": ["用", "", "用"]},
        },
        "dimensions": {
            "d": {
                "api": "D",
                "name": "维",
                "values": [{"key": "v1", "code": "C1", "name": "值一", "synonyms": ["一"]}],
                "mappings": [{"phrase": "第一", "targets": ["C1"]}, {"phrase": "别的", "targets": ["Z"]}],
            }
        },
    }
    rows = entries_from_snapshot(snap)
    assert [(x["kind"], x["id"]) for x in rows if x["kind"] in ("object", "property", "metric", "term")] == [
        ("object", "b"),
        ("property", "y"),
        ("metric", "n"),
        ("term", "K-2"),
    ]
    assert find(rows, "term", "K-2")["synonyms"] == ["用"]
    assert find(rows, "dimension", "d")["synonyms"] == ["第一", "别的"]
    assert find(rows, "dimValue", "v1") == {
        "kind": "dimValue",
        "id": "v1",
        "api": "C1",
        "parent": "D",
        "name": "值一",
        "synonyms": ["一", "第一"],
    }
