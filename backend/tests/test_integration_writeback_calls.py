"""回写调用 writeback_call（Palantir专家 第 3 批）：种子、最近调用筛选 / 分页 / counts、详情、人工核对、
测试调用落库。期望值直接取 seeds/json/system/derived/extCalls.json（= 前端 mock extCalls(sys, opsOf(sys))）。
"""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.modules.integration.models import WritebackCall
from app.modules.versioning.sections import load_sections
from app.seeds.loader import run_seeds
from app.seeds.registry import SEED_MODULES
from tests.function_access_support import grant_fixture_roles

DATA = "/api/data/v1"
ADMIN = {"X-Demo-As": "U010001"}
EXT_CALLS = Path(__file__).resolve().parents[1] / "seeds/json/system/derived/extCalls.json"
MOCK = {e["args"][0]["id"]: e["result"] for e in json.loads(EXT_CALLS.read_text("utf-8"))}


def _clean(c: dict) -> dict:
    """后台 runId 没有时给 null，mock 是不带这个键。"""
    return {k: v for k, v in c.items() if not (k == "runId" and v is None)}


@pytest.fixture
async def wb(session, _isolated_sections):
    # 灌到 integration_runs 为止（含依赖）：动态段合进来后 dynamic 种子要 ontology_release 等前置阶段；
    # 版本种子要真实 section 才能写快照（同 tests/dynamic_support.py 的 dyn）
    load_sections()
    await run_seeds(session, SEED_MODULES[: SEED_MODULES.index("integration_runs") + 1])
    await grant_fixture_roles(session)
    await session.commit()
    return session


async def test_seed_rows(wb):
    assert await wb.scalar(select(func.count()).select_from(WritebackCall)) == sum(map(len, MOCK.values()))
    n_run = await wb.scalar(select(func.count()).where(WritebackCall.action_run_id.is_not(None)))
    assert n_run == sum(1 for v in MOCK.values() for c in v if c.get("runId"))


@pytest.mark.parametrize("sys_id", ["EXT-CRM", "EXT-WMS", "EXT-SMS", "EXT-SMS-UAT"])
@pytest.mark.parametrize("result", [None, "全部", "成功", "失败", "结果未知"])
async def test_calls_match_mock(client, wb, sys_id, result):
    rows = MOCK.get(sys_id, [])
    want = [c for c in rows if result in (None, "全部") or c["result"] == result]
    q = {"result": result} if result else {}
    r = await client.get(f"{DATA}/ext-systems/{sys_id}/calls", params=q, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(want) and body["size"] == 10 and body["page"] == 1
    assert [_clean(c) for c in body["items"]] == want[:10]
    counts = {"全部": len(rows), "成功": 0, "失败": 0, "结果未知": 0}
    for c in rows:
        counts[c["result"]] += 1
    assert body["counts"] == counts
    # 越界页收拢到最后一页（前端 paginate）
    last = max(1, -(-len(want) // 10))
    params = {**q, "page": 99}
    r = await client.get(f"{DATA}/ext-systems/{sys_id}/calls", params=params, headers=ADMIN)
    assert r.json()["page"] == last
    assert [_clean(c) for c in r.json()["items"]] == want[(last - 1) * 10 : last * 10]


async def test_writeback_calls_list_and_detail(client, wb):
    r = await client.get(f"{DATA}/writeback-calls", params={"sysId": "EXT-OMS", "size": 200}, headers=ADMIN)
    assert [_clean(c) for c in r.json()["items"]] == MOCK["EXT-OMS"]
    r = await client.get(f"{DATA}/writeback-calls", params={"size": 5}, headers=ADMIN)
    assert r.json()["total"] == sum(map(len, MOCK.values())) and len(r.json()["items"]) == 5
    with_run = next(c for v in MOCK.values() for c in v if c.get("runId"))
    r = await client.get(f"{DATA}/writeback-calls/{with_run['id']}", headers=ADMIN)
    assert _clean(r.json()) == with_run
    assert (await client.get(f"{DATA}/writeback-calls/NOPE-1", headers=ADMIN)).status_code == 404
    assert (await client.get(f"{DATA}/ext-systems/EXT-NOPE/calls", headers=ADMIN)).status_code == 404
    r = await client.get(f"{DATA}/writeback-calls", params={"result": "乱写"}, headers=ADMIN)
    assert r.status_code == 422


async def test_mark_checked(client, wb):
    target = next(c for v in MOCK.values() for c in v if c["result"] == "结果未知" and not c.get("runId"))
    r = await client.post(f"{DATA}/writeback-calls/{target['id']}:mark-checked", json={}, headers=ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "成功" and body["msg"].startswith("人工核对：对方已处理（王敏 ")
    assert body["msg"].endswith(" 标记）")
    assert {k: v for k, v in body.items() if k not in ("result", "msg")} == {
        k: v for k, v in {**target, "runId": None}.items() if k not in ("result", "msg")
    }
    # 再核对一次 / 带执行记录的结果未知 / 成功的 → 409
    again = await client.post(f"{DATA}/writeback-calls/{target['id']}:mark-checked", headers=ADMIN)
    assert again.status_code == 409 and again.json()["code"] == "CALL_NOT_CHECKABLE"
    with_run = next(c for v in MOCK.values() for c in v if c["result"] == "结果未知" and c.get("runId"))
    r = await client.post(f"{DATA}/writeback-calls/{with_run['id']}:mark-checked", headers=ADMIN)
    assert r.status_code == 409
    sys_id = target["sysId"]
    counts = (await client.get(f"{DATA}/ext-systems/{sys_id}/calls", headers=ADMIN)).json()["counts"]
    assert counts["结果未知"] == sum(1 for c in MOCK[sys_id] if c["result"] == "结果未知") - 1


async def test_unconfigured_test_does_not_fabricate_calls(client, wb):
    before = await wb.scalar(select(func.count()).select_from(WritebackCall))
    for _ in range(2):
        response = await client.post(f"{DATA}/ext-systems/EXT-CRM:test", headers=ADMIN)
        assert response.status_code == 503 and response.json()["code"] == "EXT_SYSTEM_TEST_UNAVAILABLE"
    body = (await client.get(f"{DATA}/ext-systems/EXT-CRM/calls", headers=ADMIN)).json()
    assert body["total"] == len(MOCK["EXT-CRM"])
    assert [_clean(c) for c in body["items"]] == MOCK["EXT-CRM"][:10]
    assert await wb.scalar(select(func.count()).select_from(WritebackCall)) == before
