"""数据连接（api-index §3.1，Connections.vue）。

seed 与 mock 一致、列表 / 详情 / 库表 / 常量、增改删、测试连接、密码不外露。
"""

import json

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.data.models import Datasource, DatasourceRun, DatasourceTable
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

_DIR = get_settings().seeds_json_dir / "system"
DATASOURCES = json.loads((_DIR / "DATASOURCES.json").read_text("utf-8"))
DS_TABLES = json.loads((_DIR / "derived" / "dsTables.json").read_text("utf-8"))
API = "/api/data/v1"


def _load(name: str):
    return json.loads((_DIR / f"{name}.json").read_text("utf-8"))


@pytest.fixture
async def ds(users):
    await run_seeds(users, ["data"])
    await grant_fixture_roles(users)
    await users.flush()
    return users


async def _count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)))


async def test_seed_counts_and_idempotent(ds):
    want_runs = sum(len(d["runs"]) for d in DATASOURCES)
    want_tables = sum(len(x["result"]) for x in DS_TABLES)
    assert (await _count(ds, Datasource), await _count(ds, DatasourceRun)) == (16, want_runs)
    assert await _count(ds, DatasourceTable) == want_tables
    await run_seeds(ds, ["data"])
    assert (await _count(ds, Datasource), await _count(ds, DatasourceRun)) == (16, want_runs)
    assert await _count(ds, DatasourceTable) == want_tables


def _strip_none(d: dict) -> dict:
    """mock 里缺省的可选字段（compatMode、note、refOnly…）接口输出 null。"""
    return {k: v for k, v in d.items() if v is not None or k in ("lastTest",)}


async def test_list_matches_mock_exactly(client, ds):
    r = await client.get(f"{API}/datasources", params={"size": 200})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 16 and body["page"] == 1
    got = [_strip_none(x) for x in body["items"]]
    for g, m in zip(got, DATASOURCES, strict=True):
        assert {k: v for k, v in g.items() if k not in ("sync", "objectTypes", "runs")} == {
            k: v for k, v in m.items() if k not in ("sync", "objectTypes", "runs")
        }
        assert g["runs"] == []  # 列表不加载运行历史，使用独立分页读取。
        assert g["objectTypes"] == []  # 没灌静态定义，不能用连接seed的演示映射列表代替定义。
        assert g["sync"] == m["sync"]
    assert "password" not in r.text.lower()


async def test_list_filter_and_paging_like_frontend(client, ds):
    r = (await client.get(f"{API}/datasources", params={"type": "MySQL"})).json()
    assert [x["id"] for x in r["items"]] == [d["id"] for d in DATASOURCES if d["type"] == "MySQL"]
    kw = "pos"  # 库名不区分大小写；名称 / 主机区分
    want = [d["id"] for d in DATASOURCES if kw in d["name"] or kw in d["host"] or kw in d["database"].lower()]
    r = (await client.get(f"{API}/datasources", params={"kw": kw})).json()
    assert [x["id"] for x in r["items"]] == want and want
    r = (await client.get(f"{API}/datasources", params={"page": 9, "size": 12})).json()
    assert r["page"] == 2 and len(r["items"]) == 4  # 越界收拢到最后一页（paginate.ts）
    assert (await client.get(f"{API}/datasources", params={"type": "Oracle"})).status_code == 422


async def test_meta_matches_mock(client, users):
    await grant_fixture_roles(users)
    r = (await client.get(f"{API}/datasources/meta")).json()
    assert r == {
        "types": _load("DS_TYPES"),
        "defaultPort": _load("DS_DEFAULT_PORT"),
        "dmCompat": _load("DM_COMPAT"),
        "kbModes": _load("KB_MODES"),
        "erpSystems": _load("ERP_SYSTEMS"),
    }


async def test_detail_and_404(client, ds):
    r = await client.get(f"{API}/datasources/DS-002")
    assert _strip_none(r.json()) == {**DATASOURCES[1], "objectTypes": []}
    r = await client.get(f"{API}/datasources/DS-999")
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


async def test_tables_match_mock_sort_and_ref(client, ds):
    for entry in DS_TABLES:
        code = entry["args"][0]["id"]
        r = (await client.get(f"{API}/datasources/{code}/tables", params={"size": 200})).json()
        got = [{k: v for k, v in t.items() if not (k == "ref" and v is None)} for t in r["items"]]
        want = sorted(
            [{**t, "objectType": None} for t in entry["result"]], key=lambda t: (t["schema"], t["name"])
        )
        assert got == want, code
        assert r["total"] == next(d["tableCount"] for d in DATASOURCES if d["id"] == code)
    # 财务总账：门店 / 大区只作参考（参考 · 未映射）
    r = (await client.get(f"{API}/datasources/DS-002/tables", params={"kw": "store"})).json()
    store = next(t for t in r["items"] if t["name"] == "STORE")
    assert store["objectType"] is None and store["ref"].startswith("门店主数据")
    # 已映射的排在前面
    items = (await client.get(f"{API}/datasources/DS-001/tables", params={"size": 200})).json()["items"]
    flags = [t["objectType"] is not None for t in items]
    assert flags == [False] * len(flags)


async def test_tables_kw_comment_and_paging(client, ds):
    r = (await client.get(f"{API}/datasources/DS-001/tables", params={"kw": "退款", "size": 12})).json()
    assert r["total"] >= 2 and all("退款" in t["comment"] for t in r["items"])
    r = (await client.get(f"{API}/datasources/DS-001/tables", params={"page": 99, "size": 12})).json()
    assert r["page"] == -(-r["total"] // 12)


NEW = {
    "name": "测试连接库",
    "type": "达梦 DM8",
    "host": "10.0.0.9",
    "database": "TEST_DB",
    "user": "reader",
    "password": "s3cret-value",
    "compatMode": "MySQL",
    "erpSystem": "SAP S/4HANA",  # 非 ERP 类型，丢弃
    "owner": "何静",
    "mode": "手动",
    "cron": "-",
}


async def test_create_update_delete(client, ds):
    r = await client.post(f"{API}/datasources", json=NEW)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["id"] == "DS-017" and d["port"] == 5236 and d["status"] == "未测试" and d["lastTest"] is None
    assert d["compatMode"] == "MySQL" and d["erpSystem"] is None and d["runs"] == [] and d["tableCount"] == 0
    assert d["sync"] == {
        "mode": "手动",
        "cron": "-",
        "cronText": "-",
        "window": "全天",
        "cursorField": "updated_at",
        "nextRun": None,
    }
    assert "s3cret" not in r.text and "password" not in r.text.lower()
    row = await ds.scalar(select(Datasource).where(Datasource.code == "DS-017"))
    assert row.password_enc and "s3cret" not in row.password_enc
    old_hash = row.password_enc
    # 新建的排在列表最前（前端 unshift）
    first = (await client.get(f"{API}/datasources")).json()["items"][0]
    assert first["id"] == "DS-017"

    upd = {**NEW, "name": "改名", "type": "ERP", "password": "", "appId": "a-1", "cron": "0 1 * * *"}
    r = await client.put(f"{API}/datasources/DS-017", json=upd)
    assert r.status_code == 200, r.text
    d = r.json()
    assert (
        d["name"] == "改名"
        and d["port"] == 443
        and d["erpSystem"] == "SAP S/4HANA"
        and d["compatMode"] is None
    )
    assert d["sync"]["cron"] == "0 1 * * *" and d["sync"]["cronText"] == "-"
    await ds.refresh(row)
    assert row.password_enc == old_hash  # 留空 = 不修改
    await client.put(f"{API}/datasources/DS-017", json={**upd, "password": "new"})
    await ds.refresh(row)
    assert row.password_enc != old_hash

    assert (await client.delete(f"{API}/datasources/DS-017")).status_code == 204
    assert (await client.get(f"{API}/datasources/DS-017")).status_code == 404
    # 有同步记录 / 库表的 seed 数据源也能删（前端只提示影响），级联删除
    assert (await client.delete(f"{API}/datasources/DS-001")).status_code == 204
    assert (await client.get(f"{API}/datasources")).json()["total"] == 15
    assert (await client.delete(f"{API}/datasources/DS-001")).status_code == 404


async def test_create_validation(client, ds):
    r = await client.post(f"{API}/datasources", json={**NEW, "password": ""})
    assert r.status_code == 422
    r = await client.post(f"{API}/datasources", json={**NEW, "host": "  "})
    assert r.status_code == 422
    r = await client.put(f"{API}/datasources/DS-404", json=NEW)
    assert r.status_code == 404


async def test_disabled_saved_test_preserves_database_history(client, ds, monkeypatch):
    monkeypatch.delenv("RENGU_DS_REAL_CONNECT", raising=False)
    for code in ("DS-015", "DS-005"):
        before = (await client.get(f"{API}/datasources/{code}")).json()
        r = await client.post(f"{API}/datasources/{code}:test")
        assert r.status_code == 422 and r.json()["code"] == "REAL_CONNECT_UNAVAILABLE"
        assert r.json()["details"]["reason"] == "disabled"
        after = (await client.get(f"{API}/datasources/{code}")).json()
        assert (after["status"], after["lastTest"]) == (before["status"], before["lastTest"])
    assert (await client.post(f"{API}/datasources/DS-404:test")).status_code == 404


async def test_disabled_unsaved_no_write_or_fake_success(client, ds, monkeypatch):
    monkeypatch.delenv("RENGU_DS_REAL_CONNECT", raising=False)
    before = await _count(ds, Datasource)
    for body in ({"type": "MySQL", "host": "1.2.3.4"}, {"host": "1.2.3.4", "mockFail": True}):
        r = await client.post(f"{API}/datasources:test", json=body)
        assert r.status_code == 422 and r.json()["code"] == "REAL_CONNECT_UNAVAILABLE"
    assert (await client.post(f"{API}/datasources:test", json={"type": "MySQL"})).status_code == 422
    assert await _count(ds, Datasource) == before


async def test_unknown_user_401(client, ds):
    r = await client.get(f"{API}/datasources", headers={"X-Demo-As": "NOPE"})
    assert r.status_code == 401
