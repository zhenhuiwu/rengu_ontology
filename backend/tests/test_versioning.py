"""示例模块：seed 与 mock 一致、接口形状与 mock 一致、create_version 契约。"""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.contracts.versioning import SnapshotProvider, VersionService
from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.schemas import ReleaseOut
from app.modules.versioning.service import VersioningService, next_ver, snapshot_digest
from app.seeds.loader import run_seeds

_RAW = json.loads((get_settings().seeds_json_dir / "static" / "RELEASES.json").read_text("utf-8"))
MOCK = [{k: r[k] for k in ("ver", "publishedAt", "by", "note")} for r in _RAW]


# ---- seed ----
async def _by_ver(session, ver):
    return await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == ver))


async def test_seed_is_idempotent(session, sections):
    await run_seeds(session, ["versioning"])
    await run_seeds(session, ["versioning"])
    assert await session.scalar(select(func.count()).select_from(OntologyRelease)) == len(MOCK) == 5
    r = await _by_ver(session, "v2026.10.3")
    assert r.snapshot == {"objectTypes": {"order": {"label": "订单", "since": "v2026.10.3"}}}
    assert r.parent_ver == "v2026.10.2" and r.change_order_ids == ["CR-0007"]


# ---- 接口与 mock 一一对应 ----
async def test_seed_refuses_empty_snapshot(session):
    with pytest.raises(RuntimeError, match="不写空快照"):
        await run_seeds(session, ["versioning"])


async def test_list_matches_mock_exactly(client, seeded):
    body = (await client.get("/api/ontology/v1/releases")).json()
    got = [{k: it[k] for k in ("ver", "publishedAt", "by", "note")} for it in body["items"]]
    assert got == MOCK  # 不分页：字段、取值、顺序都与前端 RELEASES 相同
    assert body["current"] == "v2026.10.3" and set(body) == {"items", "current"}
    assert "total" not in body


async def test_current_and_next(client, seeded):
    body = (await client.get("/api/ontology/v1/releases/current")).json()
    assert body["current"] == "v2026.10.3"  # 前端 CURRENT_VERSION
    assert body["next"] == "v2026.10.4"  # 前端 NEXT_VERSION
    assert body["release"]["publishedAt"] == "2026-10-24 21:17"


async def test_detail_and_snapshot(client, seeded):
    body = (await client.get("/api/ontology/v1/releases/v2026.09.3")).json()
    assert body["kind"] == "发布"
    assert body["by"] == "孙浩然" and body["parentVer"] is None and body["changeOrderIds"] == ["CR-0002"]
    snap = {"objectTypes": {"order": {"label": "订单", "since": "v2026.09.3"}}}
    assert body["snapshotSha256"] == snapshot_digest(snap)
    assert (await client.get("/api/ontology/v1/releases/v2026.09.3/snapshot")).json() == snap


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
    assert e.value.code == "EMPTY_RELEASE" and e.value.status_code == 409


async def test_releases_are_append_only_in_db(seeded):
    for sql in (
        "UPDATE ontology_release SET note='x' WHERE ver='v2026.10.3'",
        "DELETE FROM ontology_release WHERE ver='v2026.10.3'",
        "TRUNCATE ontology_release",
    ):
        sp = await seeded.begin_nested()
        with pytest.raises(DBAPIError, match="只追加"):
            await seeded.execute(text(sql))
        await sp.rollback()
    # 内容不变的 UPDATE（seed 重跑）放行
    await seeded.execute(text("UPDATE ontology_release SET note=note WHERE ver='v2026.10.3'"))


async def test_rollback_creates_new_version_with_target_content(seeded):
    svc = VersioningService(FakeStaticProvider())
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    v4 = await svc.create_version(seeded, ["CR-0011"], published_by="王敏", note="毛利率口径", now=now)
    before = await svc.get_snapshot(seeded, v4)
    # 只能回滚最近一次发布：rollback_of 必须是当前最新版
    with pytest.raises(AppError) as e:
        await svc.create_version(
            seeded, ["CR-0015"], rollback_of="v2026.10.3", published_by="王敏", note="", now=now
        )
    assert e.value.code == "ROLLBACK_NOT_LATEST" and e.value.status_code == 409
    calls = []
    svc.provider.build_snapshot = lambda *a: calls.append(a)  # 回滚不调用任何 builder
    v5 = await svc.create_version(
        seeded, ["CR-0015"], rollback_of=v4, published_by="王敏", note="回滚 v2026.10.4", now=now
    )
    assert calls == []
    assert v5 == "v2026.10.5" and await svc.current_version(seeded) == v5
    assert await svc.get_snapshot(seeded, v5) == await svc.get_snapshot(seeded, "v2026.10.3")
    info = await svc.get_release(seeded, v5)
    r5 = await _by_ver(seeded, v5)
    assert r5.change_summary["restoredFrom"] == "v2026.10.3"
    assert r5.kind.name == "rollback"
    db_kind = await seeded.scalar(text("SELECT kind FROM ontology_release WHERE ver=:v"), {"v": v5})
    assert db_kind == "rollback"  # 库里英文 code
    assert ReleaseOut.model_validate(r5).model_dump(mode="json", by_alias=True)["kind"] == "回滚"  # 接口中文
    assert info.parent_ver == v4 and info.rollback_of == v4 and info.change_order_ids == ("CR-0015",)
    assert await svc.get_snapshot(seeded, v4) == before  # 旧版本不变
    assert await seeded.scalar(select(func.count()).select_from(OntologyRelease)) == 7


async def test_rollback_unknown_target(seeded):
    with pytest.raises(AppError) as e:
        await VersioningService().create_version(
            seeded, ["CR-9"], rollback_of="v1999.01.1", published_by="x", note=""
        )
    assert e.value.code == "ROLLBACK_NOT_LATEST"


async def test_publish_uses_central_clock(seeded):
    ver = await VersioningService().create_version(seeded, ["CR-0011"], published_by="王敏", note="")
    r = await _by_ver(seeded, ver)
    assert ver == "v2026.10.4"
    assert r.published_at == datetime.fromisoformat("2026-10-25T15:42:00+08:00")


async def test_sections_registry_and_default_provider(seeded):
    from app.contracts.versioning import SECTION_OWNERS
    from app.modules.versioning.sections import register_section, registered_sections

    assert SECTION_OWNERS["rules"] == SECTION_OWNERS["lifecycles"] == "dynamic"
    assert SECTION_OWNERS["actionTypes"] == "action" and SECTION_OWNERS["eventTypes"] == "event"

    class Bad:
        section = "nope"

    with pytest.raises(ValueError):
        register_section(Bad())
    with pytest.raises(ValueError):  # 同一 section 不能登记两个 builder
        register_section(type(registered_sections()["objectTypes"])())
    seeded.add(
        OntologyRelease(
            ver="v2026.10.9",
            snapshot={"objectTypes": {}, "rules": {"R-007": {"x": 1}}},
            snapshot_sha256="0" * 64,
            change_order_ids=["CR-0001"],
            change_summary={},
            note="",
            published_by="x",
            published_at=datetime(2026, 10, 25, 16, tzinfo=UTC),
        )
    )
    await seeded.flush()
    ver = await VersioningService().create_version(seeded, ["CR-0011"], published_by="王敏", note="")
    snap = await VersioningService().get_snapshot(seeded, ver)
    assert snap["rules"] == {"R-007": {"x": 1}}  # 未登记的 section 原样沿用上一版
    assert snap["objectTypes"]["order"]["applied"] == ["CR-0011"]  # 登记的 builder 组装


async def test_rollback_calls_restore_section_hooks(seeded):
    from app.modules.versioning.sections import register_section, unregister_section

    restored = []

    class Hooked:
        section = "rules"

        async def build_section(self, session, base, change_order_ids):
            return {"R-007": {"version": 9}}

        def seed_section(self, ver, ctx):
            return {}

        async def restore_section(self, session, content):
            restored.append(content)

    register_section(Hooked())
    try:
        svc = VersioningService(FakeStaticProvider())
        now = datetime(2026, 10, 25, 12, tzinfo=UTC)
        v4 = await svc.create_version(seeded, ["CR-0011"], published_by="王敏", note="", now=now)
        await svc.create_version(seeded, ["CR-0015"], rollback_of=v4, published_by="王敏", note="", now=now)
        prev = await svc.get_snapshot(seeded, "v2026.10.3")
        assert restored == [prev.get("rules")]
    finally:
        unregister_section("rules")
