"""发布写回（writeback.py）：整版发布时把 cr_item 写回定义表，快照对应节按定义表重组；
没登记写回的段（规则 / 动作 / 事件…）跳过并记进发布事件。另：:submit / :approve 的 lockVersion 恰好 +1。"""

import pytest
from sqlalchemy import select

from app.core import clock
from app.modules.static.models import ChangeRequest, CrEvent, Knowledge, KnowledgeStatus, ObjectType, Property
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.service import next_ver
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles
from tests.helpers.workspace import bind_workspace_members

P = "/api/ontology/v1"
WANG = {"X-Demo-As": "U010001"}  # 王敏（本体管理员，默认审核人）
LIU = {"X-Demo-As": "U010004"}  # 刘婷


@pytest.fixture
async def seeded(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
        await grant_fixture_roles(session)
        await bind_workspace_members(session)
        await session.commit()
        yield session


async def put(client, cr, target, section, rows):
    r = await client.put(
        f"{P}/change-requests/{cr}/items",
        json={"target": target, "section": section, "rows": rows},
        headers=LIU,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def lv(client, cr):
    return (await client.get(f"{P}/change-requests/{cr}", headers=WANG)).json()["lockVersion"]


async def approved_cr(client) -> str:
    r = await client.post(f"{P}/change-requests", json={"title": "写回测试"}, headers=LIU)
    assert r.status_code == 201, r.text
    cr = r.json()["id"]
    await put(
        client,
        cr,
        {"kind": "knowledge", "id": "K-002", "name": "x"},
        "content",
        [
            {"key": "definition", "op": "update", "before": "旧", "after": "新的定义"},
            {"key": "synonyms", "op": "update", "before": [], "after": ["甲", "乙"]},
        ],
    )
    await put(
        client,
        cr,
        {"kind": "knowledge", "id": "K-900", "name": "新词", "isNew": True},
        "content",
        [
            {"key": "name", "op": "add", "after": "新词"},
            {"key": "type", "op": "add", "after": "术语"},
            {"key": "definition", "op": "add", "after": "新词的定义"},
            {"key": "owner", "op": "add", "after": "刘婷"},
            {
                "key": "applies",
                "op": "add",
                "after": [{"kind": "object-type", "id": "store", "name": "门店"}],
            },
        ],
    )
    await put(
        client,
        cr,
        {"kind": "knowledge", "id": "K-003"},
        "status",
        [
            {"key": "status", "op": "update", "before": "已发布", "after": "已停用"},
        ],
    )
    await put(
        client,
        cr,
        {"kind": "object", "id": "order"},
        "properties",
        [
            {
                "key": "x_note",
                "op": "add",
                "after": {
                    "api": "x_note",
                    "name": "备注",
                    "type": "string",
                    "group": "其他",
                    "source": "--",
                    "desc": "选填",
                    "sensitive": True,
                },
            },
            {"key": "order_no_display", "op": "update", "before": {}, "after": {"desc": "门店对外名称"}},
        ],
    )
    await put(
        client,
        cr,
        {"kind": "rule", "id": "R-999", "isNew": True},
        "conds",
        [{"key": "-", "op": "add", "after": {"x": 1}}],
    )
    v = await lv(client, cr)
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 200, r.text
    assert await lv(client, cr) == v + 1  # 校验重算 + 改状态：一次
    r = await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)
    assert r.status_code == 200, r.text
    assert await lv(client, cr) == v + 2
    return cr


async def test_publish_writes_back(client, seeded):
    cr = await approved_cr(client)
    ver = next_ver("v2026.10.3", clock.now())
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    assert r.json()["ver"] == ver and cr in r.json()["changeOrderIds"]
    seeded.expire_all()

    k2 = await seeded.scalar(select(Knowledge).where(Knowledge.code == "K-002"))
    assert k2.definition == "新的定义" and k2.synonyms == ["甲", "乙"] and k2.cr_code == cr
    assert k2.history[0]["ver"] == ver and k2.history[0]["status"] == "生效中"
    k9 = await seeded.scalar(select(Knowledge).where(Knowledge.code == "K-900"))
    assert (k9.name, k9.type.value, k9.status, k9.since_release) == (
        "新词",
        "术语",
        KnowledgeStatus.published,
        ver,
    )
    assert [(a.target_code, a.target_name) for a in k9.applies] == [("store", "门店")]
    k3 = await seeded.scalar(select(Knowledge).where(Knowledge.code == "K-003"))
    assert k3.status == KnowledgeStatus.disabled and k3.stopped_release == ver

    store = await seeded.scalar(select(ObjectType).where(ObjectType.code == "order"))
    props = {
        p.api_name: p
        for p in await seeded.scalars(select(Property).where(Property.object_type_id == store.id))
    }
    assert props["x_note"].display_name == "备注" and props["x_note"].is_sensitive
    assert props["order_no_display"].description == "门店对外名称"

    rel = await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))
    snap = rel.snapshot
    assert snap["terms"]["K-002"]["definition"] == "新的定义"
    assert snap["terms"]["K-900"]["name"] == "新词"
    assert snap["terms"]["K-003"]["stoppedIn"] == ver
    assert snap["properties"]["order"]["x_note"]["sensitive"] is True
    assert snap["properties"]["order"]["order_no_display"]["desc"] == "门店对外名称"

    c = await seeded.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))
    e = await seeded.scalar(select(CrEvent).where(CrEvent.cr_id == c.id, CrEvent.action == "publish"))
    # 动态合并后 rule/conds 由 dynamic.writeback 登记（import 即登记），就不再记「未写回」
    from app.modules.static.writeback import HOOKS

    assert ("未写回定义表：rule/conds" in e.desc) == (("rule", "conds") not in HOOKS)


async def test_writeback_failure_rolls_back(client, seeded):
    r = await client.post(f"{P}/change-requests", json={"title": "改不存在的属性"}, headers=LIU)
    cr = r.json()["id"]
    await put(
        client,
        cr,
        {"kind": "object", "id": "order"},
        "properties",
        [
            {"key": "no_such", "op": "update", "before": {"name": "x"}, "after": {"name": "y"}},
        ],
    )
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 200, r.text
    assert (await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)).status_code == 200
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 409 and r.json()["code"] == "WRITEBACK_FAILED", r.text
    assert r.json()["details"]["key"] == "no_such"


async def test_rollback_restores_terms(client, seeded):
    """回滚刚发布的版本：terms 定义表恢复成上一版（restore_section）。"""
    k2_def = (await seeded.scalar(select(Knowledge).where(Knowledge.code == "K-002"))).definition
    await approved_cr(client)
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    ver = r.json()["ver"]
    r = await client.post(f"{P}/releases/{ver}:rollback", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    rb = r.json()["ver"]
    seeded.expire_all()
    k2 = await seeded.scalar(select(Knowledge).where(Knowledge.code == "K-002"))
    assert k2.definition == k2_def and k2.history[0]["change"] == "回滚恢复"
    k3 = await seeded.scalar(select(Knowledge).where(Knowledge.code == "K-003"))
    assert k3.status == KnowledgeStatus.published and k3.stopped_release is None
    k9 = await seeded.scalar(select(Knowledge).where(Knowledge.code == "K-900"))
    assert k9.status == KnowledgeStatus.disabled and k9.stopped_release == rb


async def test_explorer_flags_publish_snapshot_history_and_rollback(client, seeded):
    from app.modules.static.explorer import DISPLAY_FIELDS

    baseline = (await client.get(f"{P}/object-types/store/meta")).json()
    city_before = next(p for p in baseline["properties"] if p["key"] == "city")
    cr = "CR-0009"  # 门店已由这张种子草稿占用，按真实规则加入原单。
    flags = dict(
        visibility="prominent",
        searchable=True,
        filterable=True,
        sortable=True,
        listed=True,
        listWidth=180,
        format="raw",
        unit="区",
        order=99,
    )
    await put(
        client,
        cr,
        {"kind": "object", "id": "store"},
        "properties",
        [
            {"key": "city", "op": "update", "after": {**flags, "backingColumn": "phone"}},
            {"key": "area_sqm", "op": "update", "after": {"visibility": "hidden", "filterable": True}},
            {"key": "store_name", "op": "update", "after": {"sensitive": True, "filterable": True}},
        ],
    )
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 200, r.text
    assert (await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)).status_code == 200
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    ver = r.json()["ver"]
    release = await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))
    snapshot = release.snapshot["properties"]["store"]
    assert {key: snapshot["city"][key] for key in DISPLAY_FIELDS} == flags
    assert snapshot["area_sqm"]["filterable"] is False and snapshot["store_name"]["filterable"] is False
    city = await seeded.scalar(
        select(Property).join(ObjectType).where(ObjectType.code == "store", Property.api_name == "city")
    )
    assert city.backing_column == "city"  # 用户草稿不能改受控物理绑定。
    city.unit = "后来改动"
    await seeded.commit()
    old = await client.get(f"{P}/object-types/store/meta", params={"ver": ver})
    assert old.status_code == 200, old.text
    old_props = {p["key"]: p for p in old.json()["properties"]}
    assert {key: old_props["city"][key] for key in DISPLAY_FIELDS} == flags
    assert "area_sqm" not in old_props and old_props["store_name"]["filterable"] is False
    # 历史 meta 不会让敏感属性穿过真实分面查询的当前安全规则。
    facets = await client.get(f"{P}/object-types/store/facets", params={"ver": ver})
    assert facets.status_code == 200, facets.text
    assert "store_name" not in {f["key"] for f in facets.json()}
    blocked = await client.get(
        f"{P}/object-types/store/facets", params={"filters": '{"store_name":["x"]}', "ver": ver}
    )
    assert blocked.status_code == 422
    r = await client.post(f"{P}/releases/{ver}:rollback", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    seeded.expire_all()
    current = (await client.get(f"{P}/object-types/store/meta")).json()
    restored = next(p for p in current["properties"] if p["key"] == "city")
    assert restored == city_before
    # 隐藏 / 敏感标记同样随整版回滚恢复（store_name 敏感、area_sqm 隐藏都回到发布前）。
    before = {p["key"]: p for p in baseline["properties"]}
    after = {p["key"]: p for p in current["properties"]}
    assert after["area_sqm"] == before["area_sqm"] and after["store_name"] == before["store_name"]
    seeded.expire_all()
    name = await seeded.scalar(
        select(Property.is_sensitive)
        .join(ObjectType)
        .where(ObjectType.code == "store", Property.api_name == "store_name")
    )
    assert name is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("searchable", "false"),
        ("filterable", 1),
        ("sortable", None),
        ("listed", []),
        ("visibility", "public"),
        ("listWidth", True),
        ("listWidth", -1),
        ("order", -1),
        ("order", 2**32),
        ("format", {}),
        ("unit", "x\x00y"),
    ],
)
def test_explorer_flag_validation(field, value):
    from app.core.errors import AppError
    from app.modules.static.writeback import _set_prop

    prop = Property(api_name="x", visibility="normal", is_sensitive=False)
    with pytest.raises(AppError) as exc:
        _set_prop(prop, {field: value})
    assert exc.value.status_code == 409 and exc.value.details["field"] == field


def test_property_type_validation_preserves_existing_types():
    from app.core.errors import AppError
    from app.modules.static.writeback import _set_prop

    prop = Property(api_name="x", visibility="normal", is_sensitive=False)
    for typ in (
        "string",
        "int",
        "integer",
        "decimal",
        "date",
        "timestamp",
        "boolean",
        "enum",
        "json",
        "long_text",
    ):
        _set_prop(prop, {"type": typ})
        assert prop.data_type == typ
    for invalid in (["string"], {"bad": "string"}, None, "", "unknown", True, 1):
        with pytest.raises(AppError) as exc:
            _set_prop(prop, {"type": invalid})
        assert exc.value.code == "WRITEBACK_FAILED" and exc.value.details["field"] == "type"


def test_long_text_never_facets_or_sorts():
    from app.modules.static.explorer import default_flags
    from app.modules.static.writeback import _set_prop

    prop = Property(api_name="x", visibility="normal", is_sensitive=False, data_type="string")
    _set_prop(prop, {"type": "long_text", "filterable": True, "sortable": True, "searchable": True})
    assert (prop.filterable, prop.sortable, prop.searchable) == (False, False, True)
    flags = default_flags("not_seeded", {"api": "x", "type": "long_text", "dim": True})
    assert (flags["filterable"], flags["sortable"], flags["list_visible"]) == (False, False, False)


async def test_long_text_property_publishes_and_meta(client, seeded):
    cr = "CR-0009"
    await put(
        client,
        cr,
        {"kind": "object", "id": "store"},
        "properties",
        [{"key": "x_memo", "op": "add", "after": {"name": "备忘", "type": "long_text", "filterable": True}}],
    )
    assert (await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)).status_code == 200
    assert (await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)).status_code == 200
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    meta = (await client.get(f"{P}/object-types/store/meta")).json()
    memo = next(p for p in meta["properties"] if p["key"] == "x_memo")
    assert memo["dataType"] == "long_text" and not memo["filterable"] and not memo["sortable"]
    release = await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == r.json()["ver"]))
    assert release.snapshot["properties"]["store"]["x_memo"]["type"] == "long_text"


@pytest.mark.parametrize("op,invalid", [("add", ["string"]), ("update", {"bad": "string"})])
async def test_invalid_property_type_rolls_back_publish_and_keeps_draft(client, seeded, op, invalid):
    cr = "CR-0009"
    key = "invalid_new" if op == "add" else "region_code"
    await put(
        client,
        cr,
        {"kind": "object", "id": "store"},
        "properties",
        [
            {"key": "city", "op": "update", "after": {"desc": "发布失败不应写入"}},
            {"key": key, "op": op, "after": {"name": "非法类型", "type": invalid}},
        ],
    )
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 200, r.text
    assert (await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)).status_code == 200
    draft_before = (await client.get(f"{P}/change-requests/{cr}", headers=WANG)).json()
    versions_before = list(
        (await seeded.scalars(select(OntologyRelease.ver).order_by(OntologyRelease.id))).all()
    )
    props_before = (await client.get(f"{P}/object-types/store/properties?size=200")).json()
    items_before = (await client.get(f"{P}/change-requests/{cr}/items", headers=LIU)).json()
    response = await client.post(P + "/releases", json={}, headers=WANG)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "WRITEBACK_FAILED"
    assert response.json()["details"]["field"] == "type"
    assert (await client.get(f"{P}/change-requests/{cr}", headers=WANG)).json() == draft_before
    assert (await client.get(f"{P}/change-requests/{cr}/items", headers=LIU)).json() == items_before
    assert (await client.get(f"{P}/object-types/store/properties?size=200")).json() == props_before
    assert (
        list((await seeded.scalars(select(OntologyRelease.ver).order_by(OntologyRelease.id))).all())
        == versions_before
    )


async def approved_cr2(client) -> str:
    """维度取值 / 说法 / 问数设置、关系 def、指标 def、新建对象类型（isNew，全 op=add）。"""
    r = await client.post(f"{P}/change-requests", json={"title": "写回测试 2"}, headers=LIU)
    assert r.status_code == 201, r.text
    cr = r.json()["id"]
    dim = {"kind": "dimension", "id": "time", "name": "时间"}
    await put(
        client,
        cr,
        dim,
        "values",
        [
            {
                "key": "月:2027-01",
                "op": "add",
                "after": {
                    "code": "2027-01",
                    "name": "2027 年 1 月",
                    "level": "月",
                    "synonyms": ["明年一月"],
                    "status": "启用",
                },
            },
            {
                "key": "月:2026-12",
                "op": "update",
                "before": {},
                "after": {
                    "code": "2026-12",
                    "name": "2026 年 12 月",
                    "level": "月",
                    "synonyms": [],
                    "status": "停用",
                },
            },
        ],
    )
    await put(
        client,
        cr,
        dim,
        "mappings",
        [
            {
                "key": "下月>2026-11",
                "op": "add",
                "after": {"phrase": "下月", "targets": ["2026-11"], "match": "精确"},
            }
        ],
    )
    await put(
        client,
        cr,
        dim,
        "qa",
        [
            {"key": "defaultLevel", "op": "update", "before": "日", "after": "月"},
            {"key": "desc", "op": "update", "before": "", "after": "时间维度说明"},
        ],
    )
    await put(
        client,
        cr,
        {"kind": "link", "id": "order_belongs_to_store", "name": "x"},
        "def",
        [{"key": "reverse", "op": "update", "before": "", "after": "门店的订单"}],
    )
    await put(
        client,
        cr,
        {"kind": "link", "id": "new_link_1", "name": "未命名关系", "isNew": True},
        "def",
        [
            {"key": "from", "op": "add", "after": "order"},
            {"key": "name", "op": "add", "after": "订单属于新对象"},
            {"key": "api", "op": "add", "after": "orderBelongsToXObj"},
            {"key": "to", "op": "add", "after": "x_obj"},
            {"key": "card", "op": "add", "after": "N : 1"},
            {"key": "fromProp", "op": "add", "after": "order_id"},  # 源端须是 order 已有属性（V3）
            {"key": "toProp", "op": "add", "after": "id"},
        ],
    )
    await put(
        client,
        cr,
        {"kind": "metric", "id": "x_metric", "name": "新指标", "isNew": True},
        "def",
        [
            {"key": "name", "op": "add", "after": "新指标"},
            {"key": "unit", "op": "add", "after": "元"},
            {"key": "formula", "op": "add", "after": "SUM(x)"},
            {"key": "dims", "op": "add", "after": ["time"]},
        ],
    )
    obj = {"kind": "object", "id": "x_obj", "name": "新对象", "isNew": True}
    await put(
        client,
        cr,
        obj,
        "properties",
        [
            {
                "key": "id",
                "op": "add",
                "after": {
                    "api": "id",
                    "name": "编号",
                    "type": "string",
                    "group": "标识",
                    "desc": "主键",
                    "pk": True,
                },
            }
        ],
    )
    await put(
        client,
        cr,
        obj,
        "def",
        [
            {"key": "name", "op": "add", "after": "新对象"},
            {"key": "group", "op": "add", "after": "业务"},
            {"key": "desc", "op": "add", "after": "发布时新建"},
        ],
    )
    nd = {"kind": "dimension", "id": "x_dim", "name": "新维度", "isNew": True}
    await put(
        client,
        cr,
        nd,
        "values",
        [
            {
                "key": "级:A",
                "op": "add",
                "after": {"code": "A", "name": "甲", "level": "级", "synonyms": [], "status": "启用"},
            }
        ],
    )
    await put(
        client,
        cr,
        nd,
        "def",
        [
            {"key": "name", "op": "add", "after": "新维度"},
            {"key": "object", "op": "add", "after": "x_obj"},
            {"key": "levels", "op": "add", "after": [{"name": "级", "api": "lv"}]},
            {"key": "attr", "op": "add", "after": "x_id"},
        ],
    )
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 200, r.text
    r = await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)
    assert r.status_code == 200, r.text
    return cr


async def test_publish_writes_back_dims_links_metrics_new_object(client, seeded):
    cr = await approved_cr2(client)
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    ver = r.json()["ver"]
    seeded.expire_all()
    snap = (await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))).snapshot

    t = snap["dimensions"]["time"]
    vals = {v["key"]: v for v in t["values"]}
    assert vals["月:2027-01"]["since"] == ver and vals["月:2027-01"]["synonyms"] == ["明年一月"]
    assert vals["月:2026-12"]["status"] == "停用"
    assert any(m["key"] == "下月>2026-11" and m["source"] == "业务知识" for m in t["mappings"])
    assert t["qa"]["defaultLevel"] == "月" and t["desc"] == "时间维度说明"

    assert snap["links"]["order_belongs_to_store"]["reverse"] == "门店的订单"
    assert snap["links"]["order_belongs_to_store"]["cr"] == cr
    nl = snap["links"]["new_link_1"]
    assert (nl["from"], nl["to"], nl["name"], nl["since"]) == ("order", "x_obj", "订单属于新对象", ver)
    assert nl["key"] == "order_id = x_obj.id"

    assert "x_metric" in snap["metrics"]
    o = await seeded.scalar(select(ObjectType).where(ObjectType.code == "x_obj"))
    assert (o.display_name, o.obj_group, o.description) == ("新对象", "业务", "发布时新建")
    assert snap["objectTypes"]["x_obj"]["name"] == "新对象"
    assert snap["properties"]["x_obj"]["id"]["pk"] is True
    xd = snap["dimensions"]["x_dim"]
    assert (xd["name"], xd["object"], xd["attr"], xd["since"]) == ("新维度", "x_obj", "x_id", ver)
    assert [lv["name"] for lv in xd["levels"]] == ["级"] and [v["key"] for v in xd["values"]] == ["级:A"]


async def test_rollback_restores_dims_links_properties(client, seeded):
    from sqlalchemy import delete

    from app.modules.static.models import Dimension, LinkType, Metric, QaPath, QaPathStep

    prev = (
        (await seeded.scalars(select(OntologyRelease).order_by(OntologyRelease.id.desc()))).first().snapshot
    )
    await approved_cr(client)
    await approved_cr2(client)
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    ver = r.json()["ver"]
    # 发布后物理删一条没被问数路径引用的关系：回滚要按快照重建
    used = set(await seeded.scalars(select(QaPathStep.link_type_id)))
    victim = next(
        x for x in await seeded.scalars(select(LinkType)) if x.id not in used and x.code in prev["links"]
    )
    await seeded.execute(delete(LinkType).where(LinkType.id == victim.id))
    await seeded.commit()
    n_paths = len(list(await seeded.scalars(select(QaPath))))
    r = await client.post(f"{P}/releases/{ver}:rollback", json={}, headers=WANG)
    assert r.status_code == 201, r.text
    rb = r.json()["ver"]
    seeded.expire_all()
    snap = (await seeded.scalar(select(OntologyRelease).where(OntologyRelease.ver == rb))).snapshot
    assert "new_link_1" not in snap["links"]
    # 回滚掉的版本里新建的对象 / 指标 / 维度 / 关系：表里删掉
    for model, code in (
        (ObjectType, "x_obj"),
        (Metric, "x_metric"),
        (Dimension, "x_dim"),
        (LinkType, "new_link_1"),
    ):
        assert await seeded.scalar(select(model).where(model.code == code)) is None, code
    assert (
        await seeded.scalar(select(LinkType).where(LinkType.code == victim.code))
    ).display_name == victim.display_name
    assert len(list(await seeded.scalars(select(QaPath)))) == n_paths
    assert "x_obj" not in snap["objectTypes"] and "x_metric" not in snap["metrics"]
    assert snap["qaPaths"] == prev["qaPaths"]
    assert snap["links"]["order_belongs_to_store"].get("reverse") != "门店的订单"
    t = snap["dimensions"]["time"]
    assert "月:2027-01" not in {v["key"] for v in t["values"]}
    assert t["qa"]["defaultLevel"] == "日"
    assert "x_note" not in snap["properties"]["order"]
    # 定义表也回去了：之后再发布，快照按表重组也不会把回滚掉的东西带回来
    from app.modules.static.models import DimMapping, DimValue

    lk = await seeded.scalar(select(LinkType).where(LinkType.code == "order_belongs_to_store"))
    assert lk.reverse_name != "门店的订单" and lk.history[0]["change"] == "回滚恢复"
    v = await seeded.scalar(select(DimValue).where(DimValue.value_key == "月:2027-01"))
    assert v.until_release == rb
    v12 = await seeded.scalar(select(DimValue).where(DimValue.value_key == "月:2026-12"))
    assert v12.status.value == "启用"
    assert await seeded.scalar(select(DimMapping).where(DimMapping.map_key == "下月>2026-11")) is None
    order = await seeded.scalar(select(ObjectType).where(ObjectType.code == "order"))
    assert "x_note" not in {
        p.api_name for p in await seeded.scalars(select(Property).where(Property.object_type_id == order.id))
    }


async def test_missing_target_without_is_new_is_rejected(client, seeded):
    r = await client.post(f"{P}/change-requests", json={"title": "关系不存在"}, headers=LIU)
    cr = r.json()["id"]
    await put(
        client,
        cr,
        {"kind": "link", "id": "no_such_link"},
        "def",
        [{"key": "reverse", "op": "update", "before": "", "after": "x"}],
    )
    # 关系不存在且非新建：提交时 V3 即阻断（早于发布写回），不会进入发布。
    r = await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)
    assert r.status_code == 409 and r.json()["code"] == "CR_BLOCKED", r.text
    assert r.json()["details"]["blocked"] == ["V3"]


async def test_register_writeback_public(client, seeded):
    """公开登记：别的模块挂自己的 kind/section；重复登记静态钩子报错。"""
    from app.modules.static import writeback as wb

    with pytest.raises(ValueError):
        wb.register_writeback("object", "properties", lambda *a: None)
    seen = []

    async def hook(ctx, tid, rows):
        seen.append((tid, [r.item_key for r in rows], ctx.ver))
        return set()

    prev = wb.HOOKS.get(("rule", "conds"))  # 动态合并后已登记 rule/conds：临时替换，结束还原
    wb.register_writeback("rule", "conds", hook, replace=True)
    try:
        await approved_cr(client)
        r = await client.post(P + "/releases", json={}, headers=WANG)
        assert r.status_code == 201, r.text
        assert seen == [("R-999", ["-"], r.json()["ver"])]
    finally:
        if prev is None:
            wb.HOOKS.pop(("rule", "conds"), None)
        else:
            wb.HOOKS[("rule", "conds")] = prev
