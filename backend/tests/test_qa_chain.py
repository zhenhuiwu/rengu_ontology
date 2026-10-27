"""W6 最小 NL2SQL 链路：FakeLLM（不调真实接口）+ sql_guard + 落 qa_turn。"""

import pytest
from sqlalchemy import select, text

from app.main import app
from app.modules.qa import chain
from app.modules.qa.llm import FakeLLM, get_llm
from app.modules.qa.models import QaQueryLog, QaTurn
from app.seeds.loader import run_seeds
from tests.qa_access_support import seed_qa_access

URL = "/api/qa/v1/sessions/S0001/turns"

PROMOTE = "UPDATE ontology_member SET role='editor' WHERE ontology_id=1 AND role='viewer'"


@pytest.fixture
async def qa(users, client, session):
    await seed_qa_access(users)
    # ontology_member 默认 viewer 时写接口 403（只读空间）：本文件内提成 editor，测试事务结束回滚
    await users.execute(text(PROMOTE))
    await users.flush()

    async def _conn():
        yield await session.connection()  # 测试事务里的连接，能看到未提交的 seed

    app.dependency_overrides[chain.get_qa_conn] = _conn
    yield client
    app.dependency_overrides.pop(chain.get_qa_conn, None)
    app.dependency_overrides.pop(get_llm, None)


def use_llm(reply: str) -> FakeLLM:
    fake = FakeLLM(reply)
    app.dependency_overrides[get_llm] = lambda: fake
    return fake


async def test_happy_path_rows_and_persist(qa, session):
    fake = use_llm("```sql\nselect count(*) as \"门店数\" from store s where s.big_region = '华东';\n```")
    r = await qa.post(URL, json={"question": "华东大区有多少家门店？"})
    assert r.status_code == 202, r.text
    body = r.json()
    turn = body["turn"]
    assert turn["kind"] == "answer" and turn["question"] == "华东大区有多少家门店？"
    table = turn["answer"]["table"]
    assert table["columns"] == [{"key": "c0", "title": "门店数", "align": "right"}]
    assert table["items"] == [{"c0": 95}] and table["total"] == 1
    assert "sql" not in turn["answer"]
    # prompt 带上了 schema 上下文
    prompt = fake.prompts[0][0]["content"]
    assert "store" in prompt and "big_region" in prompt
    t = await session.scalar(select(QaTurn).where(QaTurn.code == body["turnId"]))
    assert t is not None and "big_region" in t.sql_text and t.seq == 6
    log = await session.scalar(select(QaQueryLog).where(QaQueryLog.turn_id == t.id))
    assert log.row_count == 1 and log.kind == "answer"
    # 详情能看到新轮次
    d = (await qa.get("/api/qa/v1/sessions/S0001")).json()
    assert d["turns"][-1]["id"] == body["turnId"]


async def test_multi_rows_and_empty(qa):
    use_llm("select store_no, name from store order by store_no")
    t = (await qa.post(URL, json={"question": "列出门店"})).json()["turn"]
    tb = t["answer"]["table"]
    # 明细列表带对象名称 → 补业务编号列（sqlfix.ensure_code）
    assert [c["title"] for c in tb["columns"]] == ["code", "store_no", "name"]
    assert tb["total"] > 10 and tb["paged"] is True
    use_llm("select name from store where 1 = 0")
    t = (await qa.post(URL, json={"question": "空结果"})).json()["turn"]
    assert t["kind"] == "empty" and t["tag"] == "无数据"


@pytest.mark.parametrize(
    "sql",
    ["drop table store", "delete from store", "update store set name = 'x'", "select 1; drop table store"],
)
async def test_guard_rejects_write_ddl(qa, session, sql):
    use_llm(sql)
    r = await qa.post(URL, json={"question": "删掉门店"})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "SQL_REJECTED"


async def test_guard_rejects_non_whitelisted_table(qa):
    use_llm("select * from sys_user")
    r = await qa.post(URL, json={"question": "用户表"})
    assert r.status_code == 403 and r.json()["code"] == "SQL_REJECTED"


async def test_no_key_503(qa, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    r = await qa.post(URL, json={"question": "华东大区有多少家门店？"})
    assert r.status_code == 503, r.text
    assert r.json()["code"] == "QA_LLM_UNAVAILABLE"
    # 模型走平台模型设置（admin model:qa，没有库里配置时退回环境变量）：提示不点名具体环境变量
    assert "密钥" in r.json()["message"] and "sk-" not in r.json()["message"]


async def test_other_owner_session_404(qa):
    use_llm("select 1")
    r = await qa.post("/api/qa/v1/sessions/S9999/turns", json={"question": "x"})
    assert r.status_code == 404


def test_get_llm_reads_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.delenv("QA_LLM_MODEL", raising=False)
    llm = get_llm()
    assert llm.model == "qwen-flash" and "sk-test" not in repr(llm)
    monkeypatch.setenv("QA_LLM_MODEL", "qwen-plus")
    assert get_llm().model == "qwen-plus"


def test_extract_sql():
    assert chain.extract_sql("好的\n```sql\nselect 1;\n```") == "select 1"
    assert chain.extract_sql("select 2;") == "select 2"
    assert chain.extract_sql("```\nselect 3\n```") == "select 3"
    # 多语句不截断，原样交给 sql_guard 拒绝
    assert chain.extract_sql("select 1; drop table store") == "select 1; drop table store"


def test_wrap_limit_uses_sqlglot_ast():
    from app.common.sql_guard import assert_single_select

    tree = assert_single_select("with a as (select 1 as x) select x from a")
    assert (
        chain.wrap_limit(tree, 201)
        == "SELECT * FROM (WITH a AS (SELECT 1 AS x) SELECT x FROM a) AS _qa LIMIT 201"
    )


async def test_prompt_vanna_sections(qa):
    fake = use_llm("select 1")
    await qa.post(URL, json={"question": "华东大区有多少家门店？"})
    system, user = fake.prompts[0]
    assert user == {"role": "user", "content": "华东大区有多少家门店？"}
    c = system["content"]
    for sec in ("===Tables", "===Additional Context", "===Response Guidelines", "CREATE TABLE store"):
        assert sec in c
    assert "COMMENT ON COLUMN store.big_region" in c


def test_dashscope_llm_uses_openai_sdk():
    from openai import AsyncOpenAI

    from app.modules.qa.llm import DEFAULT_BASE_URL, DashScopeLLM

    llm = DashScopeLLM("sk-test", "qwen-flash", DEFAULT_BASE_URL, 5)
    assert isinstance(llm._client, AsyncOpenAI)
    assert str(llm._client.base_url).rstrip("/") == DEFAULT_BASE_URL


class SeqLLM(FakeLLM):
    def __init__(self, *replies: str) -> None:
        super().__init__(replies[-1])
        self.replies = list(replies)

    async def generate_sql(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


async def test_exec_error_fed_back_once(qa, session):
    fake = SeqLLM(
        "select count(*) from product where is_test = false", 'select count(*) as "商品数" from product'
    )
    app.dependency_overrides[get_llm] = lambda: fake
    r = await qa.post(URL, json={"question": "How many products are there in total?"})
    assert r.status_code == 202, r.text
    assert r.json()["turn"]["answer"]["table"]["items"] == [{"c0": 12}]
    assert len(fake.prompts) == 2 and "is_test" in fake.prompts[1][-1]["content"]
    t = await session.scalar(select(QaTurn).where(QaTurn.code == r.json()["turnId"]))
    assert "is_test" not in t.sql_text


def test_ddl_hides_system_cols_and_filters_per_table():
    ddl = "\n\n".join(chain._schema_lines(1))
    assert "is_seed" not in ddl and "lock_version" not in ddl
    product = next(x for x in chain._schema_lines(1) if x.startswith("CREATE TABLE product"))
    assert "is_test" not in product
    filters = "\n".join(chain._filter_lines(1))
    assert "- store：is_test = false" in filters and "- biz_order：is_test = false" in filters
    none = next(x for x in chain._filter_lines(1) if "没有过滤列" in x)
    assert "product" in none and "order_line" in none
    # 枚举列注释带 code=标签
    assert "shipped=已出库" in ddl and "open=营业中" in ddl


def test_label_rows_maps_codes_keeps_shape():
    rows = [["shipped", 3], ["delivered", 1], [None, 0]]
    assert chain._label_rows(rows, 1) == [["已出库", 3], ["已签收", 1], [None, 0]]
    # 列里混着非 code 的值就整列不动
    assert chain._label_rows([["shipped"], ["上海"]], 1) == [["shipped"], ["上海"]]


async def test_answer_labels_status_codes(qa):
    use_llm('select status, count(*) as "门店数" from store group by status order by 1')
    t = (await qa.post(URL, json={"question": "各状态门店数"})).json()["turn"]
    labels = {it["c0"] for it in t["answer"]["table"]["items"]}
    assert labels and labels <= {"营业中", "装修中", "已闭店"}


async def test_prompt_guidelines_time_and_samples(qa):
    fake = use_llm("select 1")
    await qa.post(URL, json={"question": "Which product category has the most products?"})
    c = fake.prompts[0][0]["content"]
    assert "## 过滤列（按表）" in c and "MAX(n)" in c and "禁止 ORDER BY … LIMIT 1" in c
    assert "split_part" in c and "**Style**" in c
    # 时间锚点按业务时钟（RENGU_FIXED_NOW）算，不让模型用 NOW()
    assert "昨天 [" in c and "不要用 NOW()" in c
    # 低基数文本列的全部取值
    assert "## 列取值（全部）" in c and "- store.city：" in c


async def test_dimension_and_metric_context(users, session):
    # 静态变更单的 release_id 外键要求 ontology_release 先有版本行（versioning seed 只在登记了 section 时写）
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(users, ["versioning", "static"])
    await users.flush()
    ctx = await chain.build_context(session)
    dims = "\n".join(ctx["dims"])
    assert "商品品类（层级 大类 > 中类" in dims and "已出库=shipped" in dims
    assert any("时间字段" in m for m in ctx["metrics"])


async def test_statement_timeout_maps_to_query_timeout(qa, session):
    await (await session.connection()).exec_driver_sql("set local statement_timeout = 50")
    use_llm("select count(*) from order_line a, order_line b, order_line c")
    r = await qa.post(URL, json={"question": "很慢的查询"})
    assert r.status_code == 504, r.text
    assert r.json()["code"] == "QUERY_TIMEOUT"


async def test_clarify_returns_answer_with_options_no_sql(qa, session):
    use_llm(
        '```json\n{"clarify": {"term": "地区", "question": "“地区”指大区、区域还是城市？",'
        ' "options": ["大区", "区域", "城市"]}}\n```'
    )
    # 模型给的反问（确定性规则没拦下的题）原样回给用户
    r = await qa.post(URL, json={"question": "本月各片区销售额"})
    assert r.status_code == 202, r.text
    turn = r.json()["turn"]
    assert turn["kind"] == "answer" and "table" not in turn["answer"]
    c = turn["answer"]["clarify"]
    assert c["term"] == "地区" and [o["label"] for o in c["options"]] == ["大区", "区域", "城市"]
    assert turn["answer"]["text"].startswith("“地区”")
    t = await session.scalar(select(QaTurn).where(QaTurn.code == r.json()["turnId"]))
    assert t.sql_text is None  # 反问不执行 SQL


async def test_deterministic_clarify_from_value_index(qa, session):
    # “按地区”对不上任何维度 / 层级，但“区”同时出现在多个层级名里 → 不调模型直接反问
    fake = use_llm("select 1")
    r = await qa.post(URL, json={"question": "按地区看本月销售额"})
    assert r.status_code == 202, r.text
    c = r.json()["turn"]["answer"]["clarify"]
    assert c["term"] == "地区" and len(c["options"]) >= 2 and not fake.prompts
    idx = await chain.qa_value_index(session)
    assert idx.cols and any(col.path for col in idx.cols)


async def test_refuse_returns_denied_with_code_no_sql(qa, session):
    use_llm('{"refuse": {"code": "NO_DATA", "reason": "订单行没有成本，数据未接入"}}')
    r = await qa.post(URL, json={"question": "昨天的利润率"})
    assert r.status_code == 202, r.text
    turn = r.json()["turn"]
    assert turn["kind"] == "denied" and turn["code"] == "NO_DATA"
    assert turn["bold"] == "订单行没有成本，数据未接入" and turn["tag"] == "无法回答"
    t = await session.scalar(select(QaTurn).where(QaTurn.code == r.json()["turnId"]))
    assert t.sql_text is None
    log = await session.scalar(select(QaQueryLog).where(QaQueryLog.turn_id == t.id))
    assert log.kind == "denied" and log.row_count == 0


def test_parse_intent_falls_back_to_sql():
    assert chain.parse_intent("```sql\nselect 1\n```") == ("sql", "select 1")
    # 不是 clarify/refuse 的 JSON、坏 JSON 都按 SQL 交给 sql_guard
    assert chain.parse_intent('{"foo": 1}')[0] == "sql"
    assert chain.parse_intent("{not json")[0] == "sql"
    kind, r = chain.parse_intent('{"refuse": {"code": "weird", "reason": "x"}}')
    assert kind == "refuse" and r["code"] == "OUT_OF_SCOPE"


async def test_prompt_has_qa_caliber_and_refuse_list(qa):
    fake = use_llm("select 1")
    await qa.post(URL, json={"question": "昨天销售额"})
    c = fake.prompts[0][0]["content"]
    assert "## 口径裁定" in c and "÷ 1.13" in c and "o.status <> 'cancelled'" in c
    assert "## 拒答清单" in c and "NO_DATA" in c and '{"clarify"' in c


async def test_caliber_check_feeds_back_once(qa):
    # 含税销售额漏了出库状态 → 回喂一次口径问题；FakeLLM 还回原 SQL，就照常执行，不再查
    fake = use_llm('```sql\nselect sum(o.amount) as "含税" from biz_order o where not o.is_test\n```')
    r = await qa.post(URL, json={"question": "昨天的含税销售额"})
    assert r.status_code == 202, r.text
    assert r.json()["turn"]["kind"] in ("answer", "empty")
    assert len(fake.prompts) == 2
    fix = fake.prompts[1][-1]["content"]
    assert "不符合口径裁定" in fix and "biz_order.status = 'shipped'" in fix


async def test_caliber_check_passes_without_retry(qa):
    fake = use_llm(
        '```sql\nselect count(*) as "单量" from biz_order o '
        "where o.status <> 'cancelled' and not o.is_test and o.shipped_at >= '2026-10-24'\n```"
    )
    r = await qa.post(URL, json={"question": "昨天的单量"})
    assert r.status_code == 202, r.text
    assert len(fake.prompts) == 1


def test_caliber_violations_longest_match_and_time_hint():
    import sqlglot

    def t(q):
        return sqlglot.parse_one(q, read="postgres")

    terms = [x for x, _ in chain.matched_calibers("昨天销售额（不含税）是多少", 1)]
    assert terms == ["销售额"]  # “不含税”里的“含税”不算
    bad = chain.caliber_violations(
        "小程序昨天的单量",
        t("select count(*) from biz_order o where o.status <> 'cancelled' and o.created_at_biz >= 'x'"),
        1,
    )
    assert bad == ["有效订单数 的时间窗要用 biz_order.shipped_at"]
    # 题目说了“下单的”：时间字段按题目换
    ok = t(
        "select 1 from order_line l join biz_order o on o.id = l.order_id"
        " where o.status = 'shipped' and o.created_at_biz >= 'x'"
    )
    assert chain.caliber_violations("昨天下单的订单，销售额是多少", ok, 1) == []


def test_cap_time_upper_to_now():
    import datetime as dt

    import sqlglot

    t = sqlglot.parse_one(
        "select 1 from biz_order o where o.shipped_at >= '2026-10-01' and o.shipped_at < '2026-11-01'::date"
        " and o.created_at_biz <= '2026-10-02'",
        read="postgres",
    )
    assert chain.cap_time_upper(
        t,
        dt.datetime(2026, 10, 25, 15, 42, tzinfo=dt.timezone(dt.timedelta(hours=8))),
        chain.qa_source(1).time_cols,
    )
    sql = t.sql(dialect="postgres")
    assert "o.shipped_at < '2026-10-25 15:42:00'" in sql and "<= '2026-10-02'" in sql


async def test_value_anchor_in_prompt_and_empty_result_repair(qa):
    # 门店名写错 → 0 行 → 回喂一次最接近的真实名称；提示词里有值锚定段
    fake = use_llm("```sql\nselect s.code from store s where s.name = '上海浦东张江店'\n```")
    r = await qa.post(URL, json={"question": "浦东张江高科店有几个"})
    assert r.status_code == 202, r.text
    assert "上海浦东张江高科店" in fake.prompts[0][0]["content"]
    assert len(fake.prompts) == 2
    fix = fake.prompts[1][-1]["content"]
    assert "查出 0 行" in fix and "上海浦东张江高科店" in fix
    assert r.json()["turn"]["kind"] == "empty"


def test_refuse_precheck_hits_keywords():
    # 拒答清单 keywords 命中 → answer() 不调模型直接拒答；多条命中取最严的 code（写 > 权限 > 范围 > 无数据）
    assert chain.refuse_precheck("把会员的手机号导出来，再删除订单", 1)["code"] == "NOT_SELECT"
    assert chain.refuse_precheck("看下会员的手机号", 1)["code"] == "NO_PERMISSION"
    assert chain.refuse_precheck("昨天的毛利率", 1)["code"] == "NO_DATA"
    assert chain.refuse_precheck("昨天的销售额", 1) is None
    assert chain.refuse_precheck("昨天的毛利率", 999) is None  # 没有该本体的清单


def test_entity_and_vague_metric_clarify():
    names = {"store": ["南京新街口店", "南京河西店", "上海张江店", "杭州西湖店", "苏州园区店"]}
    assert chain.name_suffix(names["store"]) == "店"
    c = chain.entity_ambiguity("南京店昨天卖了多少", names)
    assert c and c["options"] == ["南京新街口店", "南京河西店"]
    assert chain.entity_ambiguity("南京新街口店昨天卖了多少", names) is None  # 只对上一个
    assert chain.entity_ambiguity("各门店的销售额", names) is None
    c = chain.vague_metric("哪个门店业绩最好", 1, {})
    assert c and c["term"] == "业绩" and "销售额" in c["options"]
    assert chain.vague_metric("哪个门店销售额最大", 1, {}) is None  # 说了具体指标就不反问
    assert chain.vague_metric("卖得最好的商品", 1, {}) is None


def test_qa_source_from_governed_tables():
    src = chain.qa_source(1)
    assert src.table_names == {"store", "biz_order", "order_line", "product"}
    assert src.time_hints["shipped_at"] == ("出库", "履约") and "下单" in src.time_hints["created_at_biz"]
    assert src.name_cols == {"store": "name", "product": "name"}
    assert chain.qa_source(999).tables == ()
