"""静态本体：迁移往返、seed 条数与 mock 一致、只读接口形状和样例记录与 mock 一致、?ver= 规则。"""

import json

import pytest
from alembic.config import Config
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.static import history
from app.modules.static.models import (
    Dimension,
    DimLevel,
    DimMapping,
    DimValue,
    Knowledge,
    KnowledgeApply,
    LinkQuality,
    LinkType,
    Metric,
    ObjectType,
    ObjectView,
    Property,
    QaPath,
    QaPathExample,
    QaPathStep,
)
from app.seeds.loader import run_seeds

P = "/api/ontology/v1"
JSON = get_settings().seeds_json_dir / "static"
STATIC_TABLES = {
    "object_type",
    "property",
    "object_view",
    "link_type",
    "link_quality",
    "metric",
    "dimension",
    "dim_level",
    "dim_value",
    "dim_mapping",
    "knowledge",
    "knowledge_apply",
    "onto_qa_path",
    "onto_qa_path_step",
    "onto_qa_path_example",
}


def load(name: str):
    return json.loads((JSON / name).read_text("utf-8"))


Q_OBJ = load("Q_OBJECT_TYPES.json")
PROPS = load("OBJECT_PROPS.json")
VIEWS = load("derived/viewsOf.json")
LINKS = load("LINKS.json")
METRIC_LIST = load("METRIC_LIST.json")
DIMS = load("DIM_ITEMS.json")
KNOWLEDGE = load("KNOWLEDGE.json")
PATH_GROUPS = load("PATH_GROUPS.json")
METRIC_USAGE = {x["args"][0]["id"]: x["result"] for x in load("derived/metricUsage.json")}


def without(d: dict, *keys: str) -> dict:
    return {k: v for k, v in d.items() if k not in keys}


@pytest.fixture
def static_sections():
    """只登记静态真实的 7 个快照 section（骨架 isolated_sections：块内隔离，结束恢复）。"""
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        yield BUILDERS


@pytest.fixture
async def static_seeded(session, static_sections):
    await run_seeds(session, ["admin", "versioning", "static"])
    await session.commit()  # 释放 savepoint：接口报错回滚时不会把种子一起回滚（外层事务结束时仍整体回滚）
    return session


async def get(client, url: str, status: int = 200):
    r = await client.get(P + url)
    assert r.status_code == status, (url, r.text[:500])
    return r.json()


async def get_all(client, url: str) -> list:
    sep = "&" if "?" in url else "?"
    body = await get(client, f"{url}{sep}size=200")
    assert body["total"] == len(body["items"])
    return body["items"]


# ---------------------------------------------------------------- 迁移


def static_parent() -> str:
    """static_0001 的上一个迁移，从 ScriptDirectory 读（现为 qa_0001），不写死。"""
    from alembic.script import ScriptDirectory

    sd = ScriptDirectory.from_config(Config("alembic.ini"))
    down = sd.get_revision("static_0001").down_revision
    assert isinstance(down, str)
    # 确认 head 链是一条线且经过 static_0001
    chain = [r.revision for r in sd.walk_revisions("base", "heads")]
    assert "static_0001" in chain and down in chain
    return down


async def test_migration_round_trip():
    """static_0001 往返：用骨架 assert_module_round_trip（不写死父迁移 / head）。"""
    from tests.helpers.migrations import assert_module_round_trip

    await assert_module_round_trip("static", STATIC_TABLES)


def test_migration_conventions():
    """static_0001 接 qa_0001（qa 先合入 master a70cba2），单一 head，无 branch_labels，无 merge。"""
    from alembic.script import ScriptDirectory

    sd = ScriptDirectory.from_config(Config("alembic.ini"))
    rev = sd.get_revision("static_0001")
    assert rev.down_revision == static_parent() == "qa_0001"
    assert not rev.branch_labels
    assert rev.path.endswith("static_0001_static_definitions.py")
    assert len(sd.get_heads()) == 1
    assert not any(r.is_merge_point for r in sd.walk_revisions())


# ---------------------------------------------------------------- seed


MOCK_COUNTS = {
    ObjectType: len(Q_OBJ),
    Property: sum(len(PROPS.get(o["key"], [])) for o in Q_OBJ),
    ObjectView: sum(len(v["result"]) for v in VIEWS),
    LinkType: len(LINKS),
    LinkQuality: sum(1 for x in LINKS if x.get("quality")),
    Metric: len(METRIC_LIST),
    Dimension: len(DIMS),
    DimLevel: sum(len(d["levels"]) for d in DIMS),
    DimValue: sum(len(d["values"]) for d in DIMS),
    DimMapping: sum(len(d["mappings"]) for d in DIMS),
    Knowledge: len(KNOWLEDGE),
    KnowledgeApply: sum(len(k["applies"]) for k in KNOWLEDGE),
    QaPath: sum(len(g["paths"]) for g in PATH_GROUPS),
    QaPathStep: sum(len(p["links"]) for g in PATH_GROUPS for p in g["paths"]),
    QaPathExample: sum(len(g["examples"]) for g in PATH_GROUPS),
}


def test_mock_counts_are_the_expected_ones():
    got = {m.__tablename__: n for m, n in MOCK_COUNTS.items()}
    assert got == {
        "object_type": 18,
        "property": 211,
        "object_view": 285,
        "link_type": 27,
        "link_quality": 26,
        "metric": 15,
        "dimension": 17,
        "dim_level": 30,
        "dim_value": 235,
        "dim_mapping": 34,
        "knowledge": 26,
        "knowledge_apply": 56,
        "onto_qa_path": 7,
        "onto_qa_path_step": 10,
        "onto_qa_path_example": 9,
    }
    assert len(PATH_GROUPS) == 3


async def test_seed_counts_match_mock_and_idempotent(static_seeded):
    from app.modules.versioning.models import OntologyRelease

    for _ in range(2):  # 第二遍验证幂等
        for model, n in MOCK_COUNTS.items():
            assert await static_seeded.scalar(select(func.count()).select_from(model)) == n, model
        assert await static_seeded.scalar(select(func.count()).select_from(OntologyRelease)) == 5
        await run_seeds(static_seeded, ["static"])
        await static_seeded.flush()


STATIC_SECTIONS = ["objectTypes", "properties", "links", "metrics", "dimensions", "terms", "qaPaths"]


def test_static_sections_discovered_by_load_sections():
    """静态 section 只经 sections.py 的 BUILDERS 由 load_sections() 发现，按 BUILDERS 顺序登记。"""
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections, load_sections

    with isolated_sections():
        names = load_sections()
    assert [n for n in names if n in STATIC_SECTIONS] == [b.section for b in BUILDERS] == STATIC_SECTIONS


def test_import_static_does_not_register_sections():
    """导入 app.modules.static（及其 sections.py）不登记任何 section（import 时登记已废弃，新进程里验证）。"""
    import subprocess
    import sys

    code = (
        "import app.modules.static, app.modules.static.sections\n"
        "from app.modules.versioning.sections import registered_sections as r\n"
        f"assert not set(r()) & set({STATIC_SECTIONS!r}), sorted(r())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-3000:]


async def test_release_snapshots_have_static_sections(static_seeded):
    from app.modules.versioning.service import VersioningService

    snap = await VersioningService().get_snapshot(static_seeded, "v2026.09.3")
    assert set(STATIC_SECTIONS) <= set(snap)
    # 历史版本：关系只含当时已发布的；业务知识只含 since ≤ ver 的
    assert set(snap["links"]) == {x["id"] for x in history.links_at(LINKS, "v2026.09.3")}
    assert set(snap["terms"]) == {k["id"] for k in KNOWLEDGE if k.get("since") and k["since"] <= "v2026.09.3"}
    assert "quality" not in next(iter(snap["links"].values()))  # 运行数据不进快照


async def test_build_section_from_tables(static_seeded):
    """发布组装：objectTypes / metrics 从当前表组装，与按 JSON 组装的当前版本一致。"""
    from app.modules.static.sections import MetricsSection, ObjectTypesSection
    from app.seeds import SeedContext

    ctx = SeedContext(JSON, root=JSON.parent)
    for cls in (ObjectTypesSection, MetricsSection):
        built = await cls().build_section(static_seeded, None, ["CR-0011"])
        assert built == cls().seed_section("v2026.10.3", ctx), cls.section
    base = {"x": {"keep": True}}
    assert await ObjectTypesSection().build_section(static_seeded, base, []) == base


async def test_qa_paths_section_from_tables(static_seeded):
    """qaPaths：从 onto_qa_path* 组装 == seed 快照里的 qaPaths 节（PATH_GROUPS 原样，键 from→to）。"""
    from app.modules.static.sections import QaPathsSection
    from app.modules.versioning.service import VersioningService
    from app.seeds import SeedContext

    built = await QaPathsSection().build_section(static_seeded, None, [])
    expect = {f"{g['from']}→{g['to']}": g for g in PATH_GROUPS}
    assert built == QaPathsSection().seed_section("v2026.10.3", SeedContext(JSON, root=JSON.parent))
    assert built == expect
    assert list(built) == list(expect)  # 组键顺序 = PATH_GROUPS / repo id 首次出现顺序，不是 object_id
    assert built == (await VersioningService().get_snapshot(static_seeded, None))["qaPaths"]
    ex = built["order→store"]["examples"][1]
    assert ex["path"] == -1  # 先追问：path_id 为空
    assert await static_seeded.scalar(select(func.count()).where(QaPath.is_default)) == len(PATH_GROUPS)
    base = {"k": 1}
    assert await QaPathsSection().build_section(static_seeded, base, []) == base


async def test_qa_paths_examples_without_paths(static_seeded):
    """组只有 examples、没有 paths 时组装成空 paths，不 KeyError。"""
    from app.modules.static.models import ObjectType, QaPathExample
    from app.modules.static.sections import QaPathsSection

    objs = {o.code: o for o in (await static_seeded.scalars(select(ObjectType))).all()}
    static_seeded.add(
        QaPathExample(
            from_object_id=objs["order"].id,
            to_object_id=objs["member"].id,
            seq=0,
            question="只有试问",
            path_id=None,
            how="先追问",
        )
    )
    await static_seeded.flush()
    built = await QaPathsSection().build_section(static_seeded, None, [])
    g = built["order→member"]
    assert g["paths"] == [] and g["examples"] == [{"q": "只有试问", "path": -1, "how": "先追问"}]


# ---------------------------------------------------------------- 对象类型


async def test_object_types(client, static_seeded):
    body = await get(client, "/object-types")
    assert set(body) == {"items", "total", "page", "size"} and body["size"] == 22 and body["total"] == 18
    first = body["items"][0]
    lo = load("LINK_OBJECTS.json")[0]
    assert first == {
        **lo,
        "props": len(PROPS[lo["id"]]),
        "count": f"{Q_OBJ[0]['count']:,}",
        "status": "已发布",
    }
    assert [x["id"] for x in (await get(client, "/object-types?kw=订单"))["items"]] == ["order", "order_line"]
    assert await get(client, "/object-types/brief") == Q_OBJ


async def test_object_type_detail_and_tabs(client, static_seeded):
    meta = {v["args"][0]: v["result"] for v in load("derived/objectMeta.json")}
    d = await get(client, "/object-types/order")
    assert {"meta", "brief", "props", "db", "status"} <= set(d)
    assert d["brief"] == Q_OBJ[0] and d["meta"] == meta["order"] and d["props"] == PROPS["order"]

    props = await get(client, "/object-types/order/properties")
    assert props["size"] == 12 and props["total"] == len(PROPS["order"]) and props["diff"] == []
    assert props["items"] == PROPS["order"][:12]
    kw = await get(client, "/object-types/order/properties?kw=金额")
    assert kw["items"] == [p for p in PROPS["order"] if "金额" in p["name"] or "金额" in p["api"]][:12]

    maps = await get(client, "/object-types/order/mappings")
    assert maps["size"] == 12 and set(maps["items"][0]) == {"prop", "col", "srcType", "transform"}

    links = await get(client, "/object-types/order/links")
    assert links == [x for x in LINKS if x["from"] == "order" and x["status"] == "已发布"]

    views = await get_all(client, "/object-types/order/usage?kind=view")
    assert views == next(v["result"] for v in VIEWS if v["args"][0] == "order")
    metrics = await get_all(client, "/object-types/order/usage?kind=metric")
    assert metrics and all("base" in m and "via" in m for m in metrics)
    err = await get(client, "/object-types/order/usage?kind=cr", 422)
    assert err["code"] == "VALIDATION_FAILED"
    fields = err["details"]["fields"]
    assert [f["field"] for f in fields] == ["kind"] and fields[0]["message"]
    await get(client, "/object-types/nope", 404)


# ---------------------------------------------------------------- 关系类型


async def test_link_types(client, static_seeded):
    body = await get(client, "/link-types")
    assert body["size"] == 18 and body["total"] == len(LINKS) and body["items"] == LINKS[:18]
    focus = await get(client, f"/link-types?focusId={LINKS[20]['id']}")
    assert focus["page"] == 2 and LINKS[20] in focus["items"]
    src = await get_all(client, "/link-types?src=order&st=已发布")
    assert src == [x for x in LINKS if x["from"] == "order" and x["status"] == "已发布"]

    x = next(x for x in LINKS if x.get("quality"))
    assert await get(client, f"/link-types/{x['id']}") == without(x, "usage")
    assert await get(client, f"/link-types/{x['id']}/quality") == x["quality"]
    usage = await get(client, f"/link-types/{x['id']}/usage")
    assert usage["size"] == 10 and usage["items"] == x["usage"][:10]

    paths = await get(client, "/link-types/paths?from=order&to=store")
    pg = next(g for g in load("PATH_GROUPS.json") if g["from"] == "order" and g["to"] == "store")
    assert paths == [pg]


def mock_paginate(rows: list, page: int, size: int) -> dict:
    """前端 src/mock/paginate.ts:10-14 原样：page = min(max(1, page), max(1, ceil(total / size)))。"""
    pages = max(1, -(-len(rows) // size))
    pg = min(max(1, page), pages)
    return {"items": rows[(pg - 1) * size : pg * size], "total": len(rows), "page": pg, "size": size}


def test_paginate_clamps_like_frontend():
    """service.paginate：先 clamp(total) 再 Page.of；focusId 先定页，再收拢。"""
    from app.common.pagination import PageParams
    from app.modules.static.service import paginate

    rows = [{"id": f"r{i}"} for i in range(25)]
    for page in (1, 2, 3, 99, 0, -3):
        p = PageParams(page=page, size=10)
        assert paginate(rows, p) == mock_paginate(rows, page, 10), page
    assert paginate(rows, PageParams(page=99, size=10))["page"] == 3  # 越界 → 最后一页，page 为收拢后的值
    assert paginate([], PageParams(page=5, size=10)) == {"items": [], "total": 0, "page": 1, "size": 10}
    assert paginate(rows, PageParams(page=1, size=10), "r24")["page"] == 3  # focusId 所在页
    assert paginate(rows, PageParams(page=99, size=10), "nope")["page"] == 3  # 找不到 focusId → 按 page 收拢


async def test_list_pagination_clamps(client, static_seeded):
    """page=0 / page=-3 → 第 1 页（不报 422）；越界 → 最后一页且 page 为收拢后的值；total=0 → page 1、空。"""
    last = -(-len(LINKS) // 18)
    for page, want in ((0, 1), (-3, 1), (999, last), (last + 1, last)):
        body = await get(client, f"/link-types?page={page}")
        assert body == mock_paginate(LINKS, page, 18), page
        assert body["page"] == want
    assert (await get(client, "/link-types?page=999&size=5"))["page"] == -(-len(LINKS) // 5)
    empty = await get(client, "/link-types?q=__no_such_link__&page=7")
    assert empty == {"items": [], "total": 0, "page": 1, "size": 18}
    usage = await get(client, f"/link-types/{LINKS[0]['id']}/usage?page=-1")
    assert usage["page"] == 1
    for bad in ("size=0", "size=201", "page=x"):
        await get(client, f"/link-types?{bad}", 422)  # size 照旧校验


# ---------------------------------------------------------------- 指标


async def test_metrics(client, static_seeded):
    body = await get(client, "/metrics")
    assert body["size"] == 18 and body["items"] == METRIC_LIST[:18]
    assert await get_all(client, "/metrics?group=销售") == [m for m in METRIC_LIST if m["group"] == "销售"]

    d = await get(client, "/metrics/gmv")
    assert (
        without(d, "def", "dims", "disallowed", "formula", "dimNames", "value", "prev", "updatedAt")
        == (METRIC_LIST[0])
    )
    assert d["def"] == load("METRIC_DEF.json") and d["dims"] == load("METRIC_DIMS.json")
    stat = next(m for m in load("METRICS.json") if m["id"] == "gmv")
    assert d["formula"] == stat["formula"] and d["dimNames"] == stat["dims"] and d["value"] == stat["value"]

    v = await get(client, "/metrics/gmv/versions")
    assert v["size"] == 10 and v["items"] == load("METRIC_VERSIONS.json")[:10]
    r = await get(client, "/metrics/gmv/refs")
    assert r["items"] == load("METRIC_REFS.json")[:10]


# ---------------------------------------------------------------- 维度与码表


async def test_dimensions(client, static_seeded):
    body = await get(client, "/dimensions")
    assert body["size"] == 18 and body["total"] == len(DIMS)
    assert body["groups"] == list(dict.fromkeys(d["group"] for d in DIMS))
    first = body["items"][0]
    assert without(first, "valueCount") == without(DIMS[0], "values", "mappings", "usage", "history")

    for d in DIMS:
        assert await get(client, f"/dimensions/{d['id']}") == without(
            d, "values", "mappings", "usage", "history"
        )
        hist = await get_all(client, f"/dimensions/{d['id']}/history")
        assert hist == d["history"]
        usage = await get_all(client, f"/dimensions/{d['id']}/usage")
        assert usage == METRIC_USAGE[d["id"]] + d["usage"], d["id"]  # metricUsage(d) + d.usage

    fmt = next(d for d in DIMS if d["id"] == "format")
    vals = await get_all(client, "/dimensions/format/values")
    pub = [{**v, **(v.get("pub") or {})} for v in fmt["values"] if not v.get("until") and not v.get("draft")]
    assert vals == pub
    assert (await get(client, "/dimensions/format/values"))["summary"].startswith(f"共 {len(pub)} 个取值")

    maps = await get(client, "/dimensions/format/mappings")
    assert maps["size"] == 8
    for m in fmt["mappings"]:
        if not m.get("draft"):
            assert any(r["phrase"] == m["phrase"] for r in maps["items"]) or maps["total"] > 8
    conf = await get_all(client, "/dimensions/format/mappings?conflict=true")
    assert all(r["conflictText"] for r in conf)

    q = await get_all(client, "/dimensions?kind=码表")
    assert [x["id"] for x in q] == [d["id"] for d in DIMS if d["kind"] == "码表"]


# ---------------------------------------------------------------- 业务知识


async def test_knowledge(client, static_seeded):
    body = await get(client, "/knowledge")
    assert body["size"] == 19 and body["total"] == len(KNOWLEDGE)
    shown = {x["id"]: x.pop("shownStatus") for x in body["items"]}
    assert body["items"] == [without(k, "refs") for k in KNOWLEDGE[:19]]
    assert shown["K-001"] == "已发布"
    k18 = await get(client, "/knowledge/K-018")
    assert k18["shownStatus"] == "草稿中"  # CR-0011 草稿里改了 K-018
    assert without(k18, "shownStatus") == without(next(k for k in KNOWLEDGE if k["id"] == "K-018"), "refs")
    refs = await get(client, "/knowledge/K-018/refs")
    assert refs["size"] == 8 and refs["items"] == next(k for k in KNOWLEDGE if k["id"] == "K-018")["refs"][:8]

    stopped = await get_all(client, "/knowledge?st=已停用")
    assert {x["id"] for x in stopped} == {k["id"] for k in KNOWLEDGE if k["status"] == "已停用"}

    sc = await get(client, "/knowledge/synonym-conflicts")
    assert sc["rule"] == load("CONFLICT_RULE.json")
    assert {c["word"] for c in sc["conflicts"]} == {"老客", "大促", "到家"}
    await get(client, "/knowledge/K-999", 404)


# ---------------------------------------------------------------- ?ver= / ?previewAs=


async def test_history_version_reads(client, static_seeded):
    ver = "v2026.09.3"
    await get(client, "/link-types?ver=v1999.01.1", 404)
    await get(client, "/object-types?ver=v1999.01.1", 404)

    links = await get_all(client, f"/link-types?ver={ver}")
    expect = history.links_at(LINKS, ver)
    assert [x["id"] for x in links] == [x["id"] for x in expect]
    assert all("usage" in x for x in links)  # 运行数据从当前表补

    terms = await get_all(client, f"/knowledge?ver={ver}")
    assert [x["id"] for x in terms] == [k["id"] for k in KNOWLEDGE if k.get("since") and k["since"] <= ver]
    assert all(x["shownStatus"] in ("已发布", "已停用") for x in terms)

    dims = await get_all(client, f"/dimensions?ver={ver}")
    assert [x["id"] for x in dims] == [d["id"] for d in DIMS if d["since"] <= ver]
    for d in DIMS:
        if d["since"] <= ver:
            hist = await get_all(client, f"/dimensions/{d['id']}/history?ver={ver}")
            assert hist == [h for h in d["history"] if h["ver"] != "—" and h["ver"] <= ver]

    # previewAs：定义数据不按人裁剪
    assert await get(client, "/metrics?previewAs=U010002") == await get(client, "/metrics")


async def test_all_static_gets_require_login(client, static_seeded):
    """每个静态 GET 都依赖 CurrentUserDep：未知 / 停用的 X-Demo-As → 401，正常身份 → 200。"""
    from app.modules.static.router import router

    ids = {"object-types": "order", "link-types": LINKS[0]["id"], "metrics": "gmv"}
    ids |= {"dimensions": DIMS[0]["id"], "knowledge": KNOWLEDGE[0]["id"]}
    urls = []
    for r in router.routes:
        path = r.path
        if "{id}" in path:
            path = path.replace("{id}", ids[path.split("/")[1]])
        urls.append(path + ("?kind=view" if path.endswith("/usage") and "object-types" in path else ""))
    assert len(urls) == len(router.routes) >= 25
    for who, status in (("U999999", 401), ("U010308", 401), ("U010006", 200)):  # 不存在 / 高伟停用 / 李娜
        for url in urls:
            r = await client.get(P + url, headers={"X-Demo-As": who})
            assert r.status_code == status, (who, url, r.text[:300])
            if status == 401:
                assert r.json()["code"] == "UNAUTHORIZED"


def test_static_routes_func_permissions():
    """功能权限 = api-index 权限表：/object-types/brief → data-quality·view，其余按所在页面 key·view。"""
    from app.modules.static.router import FUNC_OF, router

    want = {"object-types": "object-types", "link-types": "link-types", "metrics": "metrics"}
    want |= {"dimensions": "dimensions", "knowledge": "glossary"}
    for r in router.routes:
        key = "data-quality" if r.path == "/object-types/brief" else want[r.path.split("/")[1]]
        assert FUNC_OF[r.path] == key, r.path
        assert len(r.dependencies) == 1, r.path
