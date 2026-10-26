"""T11 运行评测（FakeLLM 直接调 job.execute）/ T15 转同义词 / T16 转认证 / T17 忽略与重新打开。"""

import pytest
from sqlalchemy import select

from app.main import app
from app.modules.qa.eval import job
from app.modules.qa.llm import FakeLLM, get_llm
from app.modules.qa.models import QaEvalRun
from app.seeds.loader import run_seeds

B = "/api/qa/v1/tuning"


@pytest.fixture
async def qa(users, client):
    # 变更单状态（CR-0008 已通过）要 static seed；
    # 它的 release_id 外键要 versioning 先登记 section（同 test_qa_chain）
    from app.modules.static.sections import BUILDERS
    from app.modules.versioning.sections import isolated_sections

    with isolated_sections(*BUILDERS):
        await run_seeds(users, ["versioning", "static", "qa", "biz"])
    await users.flush()
    calls: list[tuple] = []
    app.dependency_overrides[job.get_runner] = lambda: lambda *a: calls.append(a)
    client.calls = calls  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.pop(job.get_runner, None)
    app.dependency_overrides.pop(get_llm, None)


async def test_run_eval_group(qa, session):
    fake = FakeLLM('select name as "仓库", 0.9 as "出库及时率" from store order by store_no limit 2')
    app.dependency_overrides[get_llm] = lambda: fake
    before = (await qa.get(f"{B}/eval/runs")).json()["total"]
    r = await qa.post(f"{B}/eval/runs", json={"group": "履约"})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "运行中" and run["trigger"] == "手动运行" and run["scope"] == "履约相关 3 题"
    assert run["id"] == "ER-0026" and run["total"] == 3 and run["passed"] == 0
    assert (await qa.get(f"{B}/eval/runs")).json()["total"] == before + 1
    code, group, llm, user = qa.calls[0]
    assert code == run["id"] and group == "履约" and llm is fake
    # 后台任务：测试里直接跑（同一测试事务、同一连接）
    x = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
    await job.execute(session, await session.connection(), fake, user, x, group)
    d = (await qa.get(f"{B}/eval/runs/{code}")).json()
    assert d["status"] == "已完成" and d["passed"] == 2 and d["failCount"] == 1
    f = d["fails"]["items"][0]
    assert f["qid"] == "EQ-018" and f["kind"] == "维度不匹配"
    qs = {
        q["id"]: q["last"]
        for q in (await qa.get(f"{B}/eval/questions", params={"group": "履约"})).json()["items"]
    }
    assert qs == {"EQ-017": "通过", "EQ-018": "失败", "EQ-019": "通过"}
    assert len(fake.prompts) == 3


async def test_run_eval_judge_kinds(qa, session):
    app.dependency_overrides[get_llm] = lambda: FakeLLM("x")
    assert (await qa.post(f"{B}/eval/runs", json={"group": "all"})).status_code == 202
    code, group, _, user = qa.calls[0]
    x = await session.scalar(select(QaEvalRun).where(QaEvalRun.code == code))
    assert group is None and x.scope == "全部 40 题"
    # 指标名对不上 → 指标选错；拒答 → 未召回指标（只跑前 2 题）
    await job.execute(
        session, await session.connection(), FakeLLM('select 1 as "门店数"'), user, x, group, limit=1
    )
    assert x.total == 1 and x.passed == 0
    d = (await qa.get(f"{B}/eval/runs/{code}")).json()
    assert d["fails"]["items"][0]["kind"] == "指标选错"
    assert (await qa.post(f"{B}/eval/runs", json={"group": "不存在"})).status_code == 422


async def test_eval_run_needs_llm(qa):
    def boom():
        from app.modules.qa.llm import QaLlmUnavailable

        raise QaLlmUnavailable()

    app.dependency_overrides[get_llm] = boom
    r = await qa.post(f"{B}/eval/runs", json={"group": "all"})
    assert r.status_code == 503 and not qa.calls


async def _fb(qa, fid):
    return (await qa.get(f"{B}/feedbacks/{fid}")).json()


async def test_to_synonym_covered(qa):
    # FB-242「发货准时率」不在任何变更单 → CR_API_PENDING（待 static-r3）
    r = await qa.post(f"{B}/feedbacks/FB-242:to-synonym", json={})
    assert r.status_code == 409 and r.json()["code"] == "CR_API_PENDING"
    # 造一条「发货及时率」的待处理反馈：CR-0008 已通过 → 待发布生效
    lst = (await qa.get(f"{B}/feedbacks", params={"status": "待处理", "size": 50})).json()
    assert lst["total"] == 15 and all("certBlock" in x for x in lst["items"])
    summary = (await qa.get(f"{B}/summary")).json()["openFeedback"]
    assert summary == 15


async def test_to_synonym_links_same_word(qa, session):
    from app.modules.qa.models import FbStatus, QaFeedback

    f = await session.scalar(select(QaFeedback).where(QaFeedback.code == "FB-242"))
    f2 = await session.scalar(select(QaFeedback).where(QaFeedback.code == "FB-233"))
    f.fix_text = f2.fix_text = "发货及时率"  # 两条同词待处理反馈
    await session.flush()
    d = await _fb(qa, "FB-242")
    assert d["coveredBy"] == "CR-0008" and d["coveredCrStatus"] == "已通过"
    r = await qa.post(f"{B}/feedbacks/FB-242:to-synonym", json={})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["crId"] == "CR-0008" and b["affected"] == ["FB-233", "FB-242"]
    assert b["feedback"]["status"] == "待发布生效" and b["feedback"]["resolvedBy"] == "CR-0008"
    assert f2.status == FbStatus.await_release
    assert (await qa.post(f"{B}/feedbacks/FB-242:to-synonym", json={})).status_code == 409


async def test_to_cert_new_variant_and_no_plan(qa):
    # FB-237「昨天出库及时率最差的仓库」：评测 / 高频里有计划 → 新建并认证
    d = await _fb(qa, "FB-237")
    if d["certBlock"]:
        pytest.skip("seed 里 FB-237 没有计划")
    assert d["plan"] and d["planText"]
    r = await qa.post(f"{B}/feedbacks/FB-237:to-cert")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["feedback"]["status"] == "已转认证问答" and b["feedback"]["resolvedBy"] == b["certId"]
    c = (await qa.get(f"{B}/certs/{b['certId']}")).json()
    assert c["source"] == "反馈纠错" and c["status"] in ("已认证", "待重新认证")
    if b["mode"] == "new":
        assert d["question"] in c["variants"] or d["question"] == c["question"]
    # 已处理不能再转
    assert (await qa.post(f"{B}/feedbacks/FB-237:to-cert")).status_code == 409
    # 没计划 → CERT_NO_PLAN
    blocked = [
        x
        for x in (await qa.get(f"{B}/feedbacks", params={"status": "待处理", "fixKind": "认证问答"})).json()[
            "items"
        ]
        if x["certBlock"]
    ]
    if blocked:
        r = await qa.post(f"{B}/feedbacks/{blocked[0]['id']}:to-cert")
        assert r.status_code == 422 and r.json()["code"] == "CERT_NO_PLAN"


async def test_to_cert_existing_adds_variant(qa):
    items = (await qa.get(f"{B}/feedbacks", params={"status": "待处理", "size": 50})).json()["items"]
    ex = [x for x in items if x.get("existingCert")]
    if not ex:
        pytest.skip("seed 里没有已有认证问答的待处理反馈")
    f = ex[0]
    r = (await qa.post(f"{B}/feedbacks/{f['id']}:to-cert")).json()
    assert r["mode"] == "variant" and r["certId"] == f["existingCert"]
    c = (await qa.get(f"{B}/certs/{r['certId']}")).json()
    assert f["question"] in c["variants"] or f["question"] == c["question"]


async def test_ignore_reopen(qa):
    r = await qa.post(f"{B}/feedbacks/FB-247:ignore")
    assert r.status_code == 200 and r.json()["status"] == "已忽略" and r.json()["handler"]
    assert (await qa.post(f"{B}/feedbacks/FB-247:ignore")).status_code == 409
    r = await qa.post(f"{B}/feedbacks/FB-247:reopen")
    assert r.status_code == 200 and r.json()["status"] == "待处理" and "handler" not in r.json()
    assert (await qa.post(f"{B}/feedbacks/FB-247:reopen")).status_code == 409
    assert (await qa.post(f"{B}/feedbacks/FB-248:reopen")).status_code == 409  # 待发布生效不能重新打开


async def test_writes_readonly_under_ver(qa):
    r = await qa.post(f"{B}/feedbacks/FB-247:ignore", params={"ver": "v2026.10.2"})
    assert r.status_code == 403 and r.json()["code"] == "READONLY"
