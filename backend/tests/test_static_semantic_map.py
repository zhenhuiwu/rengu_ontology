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
from tests.function_access_support import grant_fixture_roles
from tests.helpers.workspace import bind_workspace_members

P = "/api/ontology/v1"
AUTHOR = {"X-Demo-As": "U010004"}
ADMIN = {"X-Demo-As": "U010001"}


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
    # 老键全部保留（static-t7 只追加键）
    assert all({"kind", "id", "api", "parent", "name", "synonyms"} <= set(x) for x in rows)
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
    value = find(rows, "dimValue", "v1")
    assert {k: value[k] for k in ("kind", "id", "api", "parent", "name", "synonyms")} == {
        "kind": "dimValue",
        "id": "v1",
        "api": "C1",
        "parent": "D",
        "name": "值一",
        "synonyms": ["一", "第一"],
    }


async def test_t7_qa_fields_from_published_snapshot(seeded):
    """static-t7：qa 要的层级 / 上级 / 默认层级 / 物理绑定 / 执行口径 / 指标同义词都从快照读。"""
    rows = await semantic_map(seeded)
    obj = find(rows, "object", "store")
    assert obj["table"] == "ORG.T_STORE"
    # 属性：api ≠ 物理列时给出 column（city → city_name），table 为所属对象表
    city = next(x for x in rows if x["kind"] == "property" and x["id"] == "city" and x["parent"] == "Store")
    assert (city["api"], city["column"], city["table"], city["type"]) == (
        "city",
        "city_name",
        "ORG.T_STORE",
        "string",
    )
    # 指标：执行口径 / 公式 / 状态 / 同义词（定义行「同义词」）；共用示例行的其他指标不继承同义词
    gmv = find(rows, "metric", "gmv")
    assert gmv["synonyms"] == ["营业额", "销售收入", "流水", "净销售额"]
    assert gmv["execDef"]["timeField"] == "订单行.出库时间" and gmv["formula"] and gmv["status"] == "已发布"
    assert gmv["desc"] == "" and gmv["unit"] == "元"
    assert find(rows, "metric", "order_cnt")["synonyms"] == []
    # 维度：有序层级 + 物理绑定 + 默认层级 + 问数设置原样
    time = find(rows, "dimension", "time")
    assert [x["api"] for x in time["levels"]] == ["year", "quarter", "month", "week", "day"]
    assert [x["no"] for x in time["levels"]] == [1, 2, 3, 4, 5]
    assert time["levels"][2] | {} == {
        "no": 3,
        "api": "month",
        "name": "月",
        "source": "cal_month",
        "object": None,
        "property": None,
        "table": "DIM.T_CALENDAR",
        "column": "cal_month",
    }
    assert time["defaultLevel"] == {"api": "day", "name": "日"}
    assert time["qa"]["nullAs"] == "--" and time["qa"]["unspecified"]
    assert time["binding"]["table"] == "DIM.T_CALENDAR" and time["binding"]["column"] == "cal_date"
    org = find(rows, "dimension", "org")
    assert [(x["api"], x["object"], x["property"], x["table"], x["column"]) for x in org["levels"]] == [
        ("region", "store", "region_code", "ORG.T_STORE", "region_code"),
        ("store", "store", "store_id", "ORG.T_STORE", "store_id"),
    ]
    assert org["binding"] == {
        "object": "store",
        "attr": "门店（所属大区）",
        "property": "region_code",
        "table": "ORG.T_STORE",
        "column": "region_code",
    }
    channel = find(rows, "dimension", "channel")
    assert channel["levels"][0]["column"] is None  # 「码表上级」无物理列，不猜
    # 取值：显式层级与上级取值
    region = find(rows, "dimValue", "大区:REG-EC")
    assert (region["level"], region["levelApi"], region["levelNo"], region["parentValue"]) == (
        "大区",
        "region",
        1,
        None,
    )
    assert region["status"] == "启用" and region["count"] == 486
    store = next(
        x for x in rows if x["kind"] == "dimValue" and x["parent"] == "dim_org" and x["levelNo"] == 2
    )
    assert store["parentName"] == "华东大区" and store["parentValue"] == "大区:REG-EC"
    month = find(rows, "dimValue", "月:2026-10")
    assert month["levelApi"] == "month" and month["parentName"] == "2026Q4" and month["parentValue"] is None


def test_t7_parent_prefers_nearest_higher_level_and_old_snapshot_defaults():
    snap = {
        "objectTypes": {"o": {"api": "O", "name": "对象"}},
        "properties": {"o": {"k": {"api": "k", "name": "键", "source": "k_col", "pk": True}}},
        "metrics": {"m": {"api": "m", "name": "m"}},
        "dimensions": {
            "d": {
                "api": "D",
                "name": "维",
                "levels": [
                    {"name": "一", "api": "l1", "source": "c1"},
                    {"name": "二", "api": "l2", "source": "对象"},
                ],
                "qa": {"defaultLevel": "l2"},
                "values": [
                    {"key": "一:A", "code": "A", "name": "甲", "level": "一"},
                    {"key": "二:A", "code": "A2", "name": "甲", "level": "二", "parent": "甲"},
                ],
            },
            "old": {"api": "OLD", "name": "旧", "values": [{"key": "x", "code": "x", "name": "x"}]},
        },
    }
    rows = entries_from_snapshot(snap)
    lv2 = find(rows, "dimValue", "二:A")
    assert lv2["parentValue"] == "一:A" and lv2["levelNo"] == 2  # 同名时取更高层级，不指向自己
    d = find(rows, "dimension", "d")
    assert d["defaultLevel"] == {"api": "l2", "name": "二"}
    assert d["levels"][0]["table"] is None and d["levels"][0]["column"] == "c1"  # 维度列没写表
    assert (d["levels"][1]["object"], d["levels"][1]["property"], d["levels"][1]["column"]) == (
        "o",
        "k",
        "k_col",
    )
    # 旧快照缺 levels / qa / execDef / synonyms：键都在，取空值
    old = find(rows, "dimension", "old")
    assert old["levels"] == [] and old["defaultLevel"] is None and old["qa"] == {}
    assert old["binding"]["column"] is None
    x = find(rows, "dimValue", "x")
    assert x["level"] is None and x["levelNo"] is None and x["parentValue"] is None
    m = find(rows, "metric", "m")
    assert m["execDef"] == {} and m["synonyms"] == [] and m["desc"] == "" and m["formula"] is None


async def test_t7_metric_synonyms_desc_via_cr_publish_and_rollback(client, seeded):
    """指标同义词/说明走变更单 metric/def（synonyms / desc），发布写定义行进快照，整版回滚恢复。"""
    from app.modules.static.models import ChangeRequest, CRStatus, Metric

    await grant_fixture_roles(seeded)
    await bind_workspace_members(seeded)
    for existing in await seeded.scalars(
        select(ChangeRequest).where(ChangeRequest.status == CRStatus.approved)
    ):
        existing.status = CRStatus.draft
    await seeded.flush()
    code = "order_cnt"
    target = {"kind": "metric", "id": code, "name": "有效订单数"}

    async def publish(rows, expect=200):
        r = await client.post(
            P + "/change-requests",
            headers=AUTHOR,
            json={"title": "指标同义词", "reason": "问数识别", "reviewer": "U010001", "target": target},
        )
        assert r.status_code == 201, r.text
        cr = r.json()
        r = await client.put(
            P + f"/change-requests/{cr['id']}/items",
            headers=AUTHOR,
            json={"target": target, "section": "def", "lockVersion": cr["lockVersion"], "rows": rows},
        )
        assert r.status_code == 200, r.text
        for action, headers in (("submit", AUTHOR), ("approve", ADMIN)):
            r = await client.post(P + f"/change-requests/{cr['id']}:{action}", headers=headers, json={})
            assert r.status_code == 200, r.text
        r = await client.post(P + "/releases", headers=ADMIN, json={})
        assert r.status_code == 201, r.text
        return r.json()

    metric = await seeded.scalar(select(Metric).where(Metric.code == code))
    version = metric.version
    first = await publish(
        [
            {"key": "synonyms", "op": "add", "after": ["订单量", "单量", "订单量"]},
            {"key": "desc", "op": "add", "after": "有效订单的笔数"},
        ]
    )
    assert first["skipped"] == [], first
    rows = await semantic_map(seeded)
    m = find(rows, "metric", code)
    assert m["synonyms"] == ["订单量", "单量"] and m["desc"] == "有效订单的笔数"
    await seeded.refresh(metric)
    assert {
        "k": "同义词",
        "v": "订单量、单量",
    } in metric.def_rows and metric.version == version  # 不升口径版本

    second = await publish(
        [
            {"key": "synonyms", "op": "update", "before": ["订单量", "单量"], "after": "成交单量，单量"},
            {"key": "desc", "op": "delete", "before": "有效订单的笔数"},
        ]
    )
    m = find(await semantic_map(seeded), "metric", code)
    assert m["synonyms"] == ["成交单量", "单量"] and m["desc"] == ""
    old = find(await semantic_map(seeded, first["ver"]), "metric", code)
    assert old["synonyms"] == ["订单量", "单量"]  # 旧版快照原样

    r = await client.post(P + f"/releases/{second['ver']}:rollback", headers=ADMIN, json={})
    assert r.status_code == 201, r.text
    await seeded.refresh(metric)
    from app.modules.static.metric_versions import meta_def

    assert meta_def(metric.def_rows) == {"synonyms": ["订单量", "单量"], "desc": "有效订单的笔数"}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("synonyms", 3),
        ("synonyms", ["好", 1]),
        ("synonyms", ["长" * 65]),
        ("synonyms", [f"w{i}" for i in range(33)]),
        ("desc", ["不是文本"]),
        ("desc", "长" * 1001),
    ],
)
async def test_t7_metric_meta_rejects_bad_value(seeded, key, value):
    from types import SimpleNamespace

    from app.core.errors import AppError
    from app.modules.static.writeback import metric_def

    ctx = SimpleNamespace(
        session=seeded, is_new=False, name=None, by="U010001", cr=SimpleNamespace(code="CR-X")
    )
    row = SimpleNamespace(item_key=key, op="update", after=value, before=None)
    with pytest.raises(AppError):
        await metric_def(ctx, "order_cnt", [row])
