# ruff: noqa: E501
"""发布写回（writeback.py）：整版发布时把 cr_item 写回定义表，快照对应节按定义表重组；
没登记写回的段（规则 / 动作 / 事件…）跳过并记进发布事件。另：:submit / :approve 的 lockVersion 恰好 +1。"""

import pytest
from sqlalchemy import select

from app.core import clock
from app.modules.static.models import ChangeRequest, CrEvent, Knowledge, KnowledgeStatus, ObjectType, Property
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.service import next_ver
from app.seeds.loader import run_seeds

P = "/api/ontology/v1"
WANG = {"X-Demo-As": "U010001"}  # 王敏（本体管理员，默认审核人）
LIU = {"X-Demo-As": "U010004"}  # 刘婷


@pytest.fixture
async def seeded(session):
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(session, ["admin", "versioning", "static"])
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
    assert "未写回定义表：rule/conds" in e.desc


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
    assert (await client.post(f"{P}/change-requests/{cr}:submit", headers=LIU)).status_code == 200
    assert (await client.post(f"{P}/change-requests/{cr}:approve", headers=WANG)).status_code == 200
    r = await client.post(P + "/releases", json={}, headers=WANG)
    assert r.status_code == 409 and r.json()["code"] == "WRITEBACK_FAILED", r.text
    assert r.json()["details"]["key"] == "no_such"
