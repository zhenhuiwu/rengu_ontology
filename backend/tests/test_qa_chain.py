"""W6 最小 NL2SQL 链路：FakeLLM（不调真实接口）+ sql_guard + 落 qa_turn。"""

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.qa import chain
from app.modules.qa.llm import FakeLLM, get_llm
from app.modules.qa.models import QaQueryLog, QaTurn
from app.seeds.loader import run_seeds

URL = "/api/qa/v1/sessions/S0001/turns"


@pytest.fixture
async def qa(users, client, session):
    await run_seeds(users, ["qa", "biz"])
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
    assert [c["title"] for c in tb["columns"]] == ["store_no", "name"]
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
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    r = await qa.post(URL, json={"question": "华东大区有多少家门店？"})
    assert r.status_code == 503, r.text
    assert r.json()["code"] == "QA_LLM_UNAVAILABLE"
    assert "DASHSCOPE_API_KEY" in r.json()["message"]


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
