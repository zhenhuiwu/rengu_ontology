"""示例模块：seed 与 mock 一致、接口形状与 mock 一致、create_version 契约。"""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.contracts.versioning import SnapshotProvider, VersionService
from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.versioning.models import OntRelease
from app.modules.versioning.schemas import ReleaseOut
from app.modules.versioning.service import VersioningService, next_ver, snapshot_digest
from app.seeds.loader import run_seeds

MOCK = json.loads((get_settings().seeds_json_dir / "versioning" / "releases.json").read_text("utf-8"))


# ---- seed ----
async def test_seed_is_idempotent(session):
    await run_seeds(session, ["versioning"])
    await run_seeds(session, ["versioning"])
    assert await session.scalar(select(func.count()).select_from(OntRelease)) == len(MOCK) == 5
    r = await session.get(OntRelease, "v2026.10.3")
    assert r.parent_ver == "v2026.10.2" and r.change_order_ids == ["CR-0007"]


# ---- 接口与 mock 一一对应 ----
async def test_list_matches_mock_exactly(client, seeded):
    body = (await client.get("/api/ontology/v1/releases")).json()
    assert body["total"] == 5 and body["page"] == 1 and body["size"] == 20
    got = [{k: it[k] for k in ("ver", "publishedAt", "by", "note")} for it in body["items"]]
    assert got == MOCK  # 字段、取值、顺序都与前端 RELEASES 相同


async def test_list_paging(client, seeded):
    body = (await client.get("/api/ontology/v1/releases?page=2&size=2")).json()
    assert [i["ver"] for i in body["items"]] == ["v2026.10.1", "v2026.09.4"]
    assert body["total"] == 5


async def test_current_and_next(client, seeded):
    body = (await client.get("/api/ontology/v1/releases/current")).json()
    assert body["current"] == "v2026.10.3"  # 前端 CURRENT_VERSION
    assert body["next"] == "v2026.10.4"  # 前端 NEXT_VERSION
    assert body["release"]["publishedAt"] == "2026-10-24 21:17"


async def test_detail_and_snapshot(client, seeded):
    body = (await client.get("/api/ontology/v1/releases/v2026.09.3")).json()
    assert body["kind"] == "发布"
    assert body["by"] == "孙浩然" and body["parentVer"] is None and body["changeOrderIds"] == ["CR-0002"]
    assert body["snapshotSha256"] == snapshot_digest({})
    assert (await client.get("/api/ontology/v1/releases/v2026.09.3/snapshot")).json() == {}


# ---- create_version 契约 ----
class FakeStaticProvider:
    async def build_snapshot(self, session, base_ver, change_order_ids):
        return {"base": base_ver, "applied": change_order_ids, "objects": {"store": {"label": "门店"}}}


def test_implements_contracts():
    assert isinstance(VersioningService(), VersionService)
    assert isinstance(FakeStaticProvider(), SnapshotProvider)


def test_next_ver_rules():
    oct_ = datetime(2026, 10, 25, 4, tzinfo=UTC)
    assert next_ver("v2026.10.3", oct_) == "v2026.10.4"
    assert next_ver("v2026.10.3", datetime(2026, 11, 1, 1, tzinfo=UTC)) == "v2026.11.1"
    # 业务时区：UTC 10-31 16:30 已经是北京时间 11 月
    assert next_ver("v2026.10.9", datetime(2026, 10, 31, 16, 30, tzinfo=UTC)) == "v2026.11.1"
    assert next_ver(None, oct_) == "v2026.10.1"


async def test_create_version_in_callers_transaction(seeded):
    svc = VersioningService(FakeStaticProvider())
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    async with seeded.begin_nested() as sp:  # 模拟静态模块的发布事务
        ver = await svc.create_version(seeded, ["CR-0011"], published_by="王敏", note="毛利率口径", now=now)
        assert ver == "v2026.10.4"
        assert await svc.current_version(seeded) == "v2026.10.4"
        snap = await svc.get_snapshot(seeded)
        assert snap["base"] == "v2026.10.3" and snap["applied"] == ["CR-0011"]
        info = await svc.get_release(seeded, ver)
        assert info.parent_ver == "v2026.10.3" and info.snapshot_sha256 == snapshot_digest(snap)
        await sp.rollback()  # 调用方后续步骤失败 → 整体回滚，版本号不被占用
    assert await svc.current_version(seeded) == "v2026.10.3"


async def test_create_version_requires_change_orders(seeded):
    with pytest.raises(AppError) as e:
        await VersioningService().create_version(seeded, [], published_by="王敏", note="")
    assert e.value.code == "EMPTY_RELEASE"


async def test_releases_are_append_only_in_db(seeded):
    for sql in (
        "UPDATE ont_release SET note='x' WHERE ver='v2026.10.3'",
        "DELETE FROM ont_release WHERE ver='v2026.10.3'",
    ):
        sp = await seeded.begin_nested()
        with pytest.raises(DBAPIError, match="只追加"):
            await seeded.execute(text(sql))
        await sp.rollback()
    # 内容不变的 UPDATE（seed 重跑）放行
    await seeded.execute(text("UPDATE ont_release SET note=note WHERE ver='v2026.10.3'"))


async def test_rollback_creates_new_version_with_target_content(seeded):
    svc = VersioningService(FakeStaticProvider())
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    v4 = await svc.create_version(seeded, ["CR-0011"], published_by="王敏", note="毛利率口径", now=now)
    before = await svc.get_snapshot(seeded, v4)
    # 只能回滚最近一次发布：目标必须是当前版本的上一版
    with pytest.raises(AppError) as e:
        await svc.create_version_from(seeded, "v2026.10.1", "CR-0015", published_by="王敏", note="", now=now)
    assert e.value.code == "ROLLBACK_NOT_LATEST"
    v5 = await svc.create_version_from(
        seeded, "v2026.10.3", "CR-0015", published_by="王敏", note="回滚 v2026.10.4", now=now
    )
    assert v5 == "v2026.10.5" and await svc.current_version(seeded) == v5
    assert await svc.get_snapshot(seeded, v5) == await svc.get_snapshot(seeded, "v2026.10.3")
    info = await svc.get_release(seeded, v5)
    r5 = await seeded.get(OntRelease, v5)
    assert r5.kind.name == "rollback"
    db_kind = await seeded.scalar(text("SELECT kind FROM ont_release WHERE ver=:v"), {"v": v5})
    assert db_kind == "rollback"  # 库里英文 code
    assert ReleaseOut.model_validate(r5).model_dump(mode="json", by_alias=True)["kind"] == "回滚"  # 接口中文
    assert info.parent_ver == v4 and info.rollback_of == v4 and info.change_order_ids == ("CR-0015",)
    assert await svc.get_snapshot(seeded, v4) == before  # 旧版本不变
    assert await seeded.scalar(select(func.count()).select_from(OntRelease)) == 7


async def test_rollback_unknown_target(seeded):
    with pytest.raises(AppError) as e:
        await VersioningService().create_version_from(seeded, "v1999.01.1", "CR-9", published_by="x", note="")
    assert e.value.code == "NOT_FOUND"


async def test_publish_uses_central_clock(seeded):
    ver = await VersioningService().create_version(seeded, ["CR-0011"], published_by="王敏", note="")
    r = await seeded.get(OntRelease, ver)
    assert ver == "v2026.10.4"
    assert r.published_at == datetime.fromisoformat("2026-10-25T15:42:00+08:00")
