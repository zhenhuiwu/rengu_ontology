"""auto_ontology 表结构识别库：内省 → 规则草稿 → 大模型润色（假 LLM / 没 key 退回规则）。不落库、不走接口。

表结构在测试事务里临时建一个 schema（ao_test），用例结束随事务回滚。
"""

import json
import os

import pytest
from sqlalchemy import text

from app.modules.auto_ontology.generate import NO_KEY_WARNING, generate
from app.modules.auto_ontology.introspect import SchemaNotFound, TablesNotFound
from app.modules.auto_ontology.llm import DashScopeLLM, FakeLLM, LlmError, get_llm, merge, parse_reply

DDL = [
    "CREATE SCHEMA ao_test",
    "CREATE TABLE ao_test.t_region (region_id varchar(16) PRIMARY KEY, region_name varchar(64) NOT NULL)",
    "COMMENT ON TABLE ao_test.t_region IS '大区'",
    "COMMENT ON COLUMN ao_test.t_region.region_name IS '大区名称'",
    """CREATE TABLE ao_test.t_store (
        store_id varchar(16) PRIMARY KEY,
        store_name varchar(64),
        region_id varchar(16) REFERENCES ao_test.t_region(region_id),
        open_date date,
        area numeric(10,2),
        is_active boolean,
        created_at timestamptz)""",
    "COMMENT ON TABLE ao_test.t_store IS '门店，线下销售网点'",
    "COMMENT ON COLUMN ao_test.t_store.region_id IS '所属大区'",
    """CREATE TABLE ao_test.t_store_profile (
        profile_id serial PRIMARY KEY,
        store_id varchar(16) UNIQUE NOT NULL REFERENCES ao_test.t_store(store_id),
        manager varchar(32))""",
    "CREATE TABLE ao_test.t_product (product_id bigint PRIMARY KEY, sku_name varchar(64), price numeric)",
    """CREATE TABLE ao_test.t_store_product (
        store_id varchar(16) REFERENCES ao_test.t_store(store_id),
        product_id bigint REFERENCES ao_test.t_product(product_id),
        PRIMARY KEY (store_id, product_id))""",
    "COMMENT ON TABLE ao_test.t_store_product IS '门店经营商品'",
    "CREATE TABLE ao_test.t_log (msg text, store_id varchar(16) REFERENCES ao_test.t_store(store_id))",
]

LLM_REPLY = {
    "objectTypes": [
        {
            "id": "t_store",
            "name": "门店",
            "group": "组织",
            "desc": "线下门店",
            "pk": "hack",  # 结构字段：必须被忽略
            "props": [
                {"api": "region_id", "name": "所属大区", "desc": "门店归属的大区"},
                {"api": "area", "name": "经营面积", "type": "string"},
                {"api": "not_a_column", "name": "幻觉列"},
            ],
        },
        {"id": "t_ghost", "name": "不存在的对象"},
    ],
    "linkTypes": [{"id": "t_store_region", "name": "所在大区", "reverse": "大区下门店", "card": "N : M"}],
}


@pytest.fixture
async def ao(session):
    database = session.get_bind().engine.url.database
    assert database.startswith(("rengu_test", "rengu_fix_"))
    # Only the fixture owner creates this rollback-only source schema; runtime remains scoped/readonly.
    await session.execute(text("RESET ROLE"))
    try:
        for s in DDL:
            await session.execute(text(s))
        await session.execute(text("GRANT USAGE ON SCHEMA ao_test TO rengu_ontology_scoped"))
        await session.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA ao_test TO rengu_ontology_scoped"))
    finally:
        await session.execute(text("SET LOCAL ROLE rengu_ontology_scoped"))
    await session.flush()
    return session


async def gen(session, schema="ao_test", **kw):
    g = await generate(session, schema, **kw)
    return {
        "tables": g.tables,
        "proposal": g.proposal,
        "warnings": g.warnings,
        "generator": g.generator,
        "model": g.model,
    }


def obj(d, oid):
    return next(o for o in d["proposal"]["objectTypes"] if o["id"] == oid)


def prop(o, api):
    return next(p for p in o["props"] if p["api"] == api)


def link(d, frm, to):
    return [lk for lk in d["proposal"]["linkTypes"] if lk["from"] == frm and lk["to"] == to]


# ---------------------------------------------------------------- 规则生成


async def test_heuristic_draft_structure(ao):
    d = await gen(ao)  # use_llm 默认 True、llm=None：模拟没配 key
    assert d["generator"] == "规则" and d["model"] is None
    ids = {o["id"] for o in d["proposal"]["objectTypes"]}
    # 纯外键中间表不成对象，变成 N:M 关系
    assert ids == {"t_region", "t_store", "t_store_profile", "t_product", "t_log"}
    assert len(d["proposal"]["linkTypes"]) == 4
    assert sorted(d["tables"]) == sorted(
        ["t_log", "t_product", "t_region", "t_store", "t_store_product", "t_store_profile"]
    )

    store = obj(d, "t_store")
    assert store["name"] == "门店" and store["api"] == "TStore" and store["table"] == "t_store"
    assert store["pk"] == "store_id" and store["group"] == "未分组"
    types = {p["api"]: p["type"] for p in store["props"]}
    assert types == {
        "store_id": "string",
        "store_name": "string",
        "region_id": "string",
        "open_date": "date",
        "area": "decimal",
        "is_active": "boolean",
        "created_at": "timestamp",
    }
    assert prop(store, "store_id")["pk"] is True and prop(store, "store_id")["group"] == "标识"
    assert prop(store, "store_name").get("title") is True
    assert prop(store, "created_at")["group"] == "时间"
    assert prop(store, "region_id")["desc"].startswith("外键 → 大区（region_id）")
    assert prop(store, "region_id")["name"] == "所属大区"
    assert prop(store, "open_date")["name"] == "Open Date"  # 无注释：to_term_name 兜底
    for p in store["props"]:
        assert set(p) >= {"api", "name", "type", "group", "source", "desc"}

    (n1,) = link(d, "t_store", "t_region")
    assert n1["card"] == "N : 1" and n1["key"] == "region_id = t_region.region_id"
    assert n1["join"] == "t_store.region_id = t_region.region_id" and n1["nullable"] is True
    assert n1["status"] == "草稿" and n1["reverse"] == "门店列表"
    (one,) = link(d, "t_store_profile", "t_store")
    assert one["card"] == "1 : 1" and one["nullable"] is False
    (nm,) = link(d, "t_store", "t_product") + link(d, "t_product", "t_store")
    assert nm["card"] == "N : M" and nm["via"] == "t_store_product" and nm["name"] == "门店经营商品"
    assert "t_store_product.product_id" in nm["join"] and "t_store_product.store_id" in nm["join"]

    assert NO_KEY_WARNING in d["warnings"]


async def test_table_subset_and_out_of_scope_fk(ao):
    d = await gen(ao, tables=["t_store", "t_store"], use_llm=False)
    assert d["tables"] == ["t_store"] and d["proposal"]["linkTypes"] == []
    assert any("t_region 不在本次范围内" in w for w in d["warnings"])
    assert not any("DASHSCOPE_API_KEY" in w for w in d["warnings"])  # use_llm=False 不提示


async def test_input_errors(ao):
    with pytest.raises(SchemaNotFound):
        await gen(ao, schema="no_such_schema")
    with pytest.raises(TablesNotFound) as e:
        await gen(ao, tables=["t_store", "nope"])
    assert e.value.details["tables"] == ["nope"]


async def test_public_schema_of_the_app_itself(session):
    d = await gen(session, schema="public", tables=["object_type", "property", "link_type"], use_llm=False)
    assert len(d["proposal"]["objectTypes"]) == 3
    assert "alembic_version" not in d["tables"]
    assert any(lk["from"] == "property" and lk["to"] == "object_type" for lk in d["proposal"]["linkTypes"])


# ---------------------------------------------------------------- 大模型（只改名字和说明）


async def test_llm_enriches_names_but_not_structure(ao):
    fake = FakeLLM(json.dumps(LLM_REPLY, ensure_ascii=False))
    base = await gen(ao, use_llm=False)
    d = await gen(ao, llm=fake)
    assert d["generator"] == "大模型" and d["model"] == "fake-llm"
    assert len(fake.calls) == 1
    sent = json.loads(fake.calls[0][1]["content"])
    assert {o["id"] for o in sent["objectTypes"]} == {o["id"] for o in base["proposal"]["objectTypes"]}

    store = obj(d, "t_store")
    assert (store["group"], store["desc"], store["pk"]) == ("组织", "线下门店", "store_id")
    assert prop(store, "area")["name"] == "经营面积" and prop(store, "area")["type"] == "decimal"
    assert prop(store, "region_id")["desc"] == "外键 → 大区（region_id）；门店归属的大区"
    assert not any(p["api"] == "not_a_column" for p in store["props"])
    assert "t_ghost" not in {o["id"] for o in d["proposal"]["objectTypes"]}
    (lk,) = link(d, "t_store", "t_region")
    assert (lk["name"], lk["reverse"], lk["card"]) == ("所在大区", "大区下门店", "N : 1")
    # 结构与规则版完全一致
    shape = lambda x: (  # noqa: E731
        [
            (o["id"], o["pk"], [(p["api"], p["type"]) for p in o["props"]])
            for o in x["proposal"]["objectTypes"]
        ],
        [(k["id"], k["from"], k["to"], k["card"], k["key"]) for k in x["proposal"]["linkTypes"]],
    )
    assert shape(d) == shape(base)


@pytest.mark.parametrize(
    ("reply", "hint"),
    [
        (LlmError("大模型调用失败（HTTP 500）"), "HTTP 500"),
        ("抱歉我不能", "没有返回 JSON"),
        ("{bad", "没有返回 JSON"),
    ],
)
async def test_llm_failure_falls_back_to_heuristic(ao, reply, hint):
    d = await gen(ao, llm=FakeLLM(reply))
    assert d["generator"] == "规则" and d["model"] is None
    assert any(hint in w and "退回规则" in w for w in d["warnings"])
    assert obj(d, "t_store")["group"] == "未分组"


def test_parse_and_merge_units():
    assert parse_reply('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LlmError):
        parse_reply("[1, 2]")
    p = {
        "objectTypes": [{"id": "x", "name": "X", "group": "未分组", "desc": "", "props": []}],
        "linkTypes": [],
    }
    assert merge(p, {"objectTypes": [{"id": "x", "name": "  ", "group": 3}], "linkTypes": "junk"}) == 0
    assert (
        merge(p, {"objectTypes": [{"id": "x", "name": "某对象"}]}) == 1
        and p["objectTypes"][0]["name"] == "某对象"
    )


def test_get_llm_without_key_and_repr_hides_key(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    assert get_llm() is None
    assert "sk-secret" not in repr(
        DashScopeLLM(api_key="sk-secret", model="m", base_url="http://x", timeout_s=1)
    )


@pytest.mark.skipif(not os.environ.get("DASHSCOPE_API_KEY"), reason="没有 DASHSCOPE_API_KEY，跳过实连大模型")
async def test_live_llm_smoke(ao):
    d = await gen(ao, tables=["t_region", "t_store"], llm=get_llm())
    assert d["generator"] in ("大模型", "规则") and len(d["proposal"]["objectTypes"]) == 2
