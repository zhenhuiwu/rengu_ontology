"""示例模块：seed 与 mock 一致、接口形状与 mock 一致、create_version 契约。"""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.contracts.versioning import SnapshotProvider, VersionService
from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.admin.models import SysUser
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
    await run_seeds(session, ["admin", "versioning"])
    await run_seeds(session, ["admin", "versioning"])
    assert await session.scalar(select(func.count()).select_from(OntologyRelease)) == len(MOCK) == 5
    r = await _by_ver(session, "v2026.10.3")
    assert r.snapshot == {"objectTypes": {"order": {"label": "订单", "since": "v2026.10.3"}}}
    assert r.parent_ver == "v2026.10.2" and r.change_order_ids == ["CR-0007"]


# ---- 接口与 mock 一一对应 ----
async def test_seed_skips_without_sections(users, monkeypatch):
    """还没有模块登记 section（骨架阶段）：versioning seed 跳过并记日志，不写空快照、不报错。"""
    from app.modules.versioning.sections import registered_sections, seed_snapshot
    from app.seeds import loader

    logged: list[str] = []  # app.seeds logger 不向上传播，caplog 收不到，直接截 info
    monkeypatch.setattr(loader._log, "info", lambda msg, *a: logged.append(msg % a))
    assert not registered_sections()
    reports = await run_seeds(users, ["versioning"])
    assert reports == [] and any("没有模块登记快照 section" in m for m in logged)
    assert await users.scalar(select(func.count()).select_from(OntologyRelease)) == 0
    with pytest.raises(RuntimeError, match="不写空快照"):  # 直接调用仍拒绝空快照
        seed_snapshot("v2026.10.3", None)


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
        ver = await svc.create_version(
            seeded, ["CR-0011"], published_by="U010001", note="毛利率口径", now=now
        )
        assert ver == "v2026.10.4"
        assert await svc.current_version(seeded) == "v2026.10.4"
        snap = await svc.get_snapshot(seeded)
        assert snap["base"] == "v2026.10.3" and snap["applied"] == ["CR-0011"]
        info = await svc.get_release(seeded, ver)
        assert info.parent_ver == "v2026.10.3" and info.snapshot_sha256 == snapshot_digest(snap)
        assert (info.published_by, info.published_by_code) == ("王敏", "U010001")
        await sp.rollback()  # 调用方后续步骤失败 → 整体回滚，版本号不被占用
    assert await svc.current_version(seeded) == "v2026.10.3"


async def test_create_version_requires_change_orders(seeded):
    with pytest.raises(AppError) as e:
        await VersioningService().create_version(seeded, [], published_by="U010001", note="")
    assert e.value.code == "EMPTY_RELEASE" and e.value.status_code == 409


APPEND_ONLY_VIOLATIONS = (
    "UPDATE ontology_release SET note='x' WHERE ver='v2026.10.3'",
    "DELETE FROM ontology_release WHERE ver='v2026.10.3'",
    # 有别的表外键指向 ontology_release 时，不带 CASCADE 的 TRUNCATE 在触发器之前就被外键检查拒绝
    # （"referenced in a foreign key constraint"）；CASCADE 时 PG 先对**所有**要清空的表触发
    # BEFORE TRUNCATE 语句级触发器，再动数据，所以照样被只追加触发器拦下。
    "TRUNCATE ontology_release CASCADE",
)


async def _assert_append_only(session):
    for sql in APPEND_ONLY_VIOLATIONS:
        sp = await session.begin_nested()  # 每条在 savepoint 里执行，失败后回滚，不影响后续
        with pytest.raises(DBAPIError, match="ontology_release 只追加"):
            await session.execute(text(sql))
        await sp.rollback()
    # 内容不变的 UPDATE（seed 重跑）放行
    await session.execute(text("UPDATE ontology_release SET note=note WHERE ver='v2026.10.3'"))
    assert await session.scalar(text("SELECT count(*) FROM ontology_release")) == len(MOCK)


async def test_release_triggers_are_before_statement_truncate_and_row_update_delete(seeded):
    rows = (
        await seeded.execute(
            text(
                "SELECT tgname, tgtype FROM pg_trigger"
                " WHERE tgrelid = 'ontology_release'::regclass AND NOT tgisinternal ORDER BY tgname"
            )
        )
    ).all()
    t = dict(rows)
    row, before, delete, update, truncate = 1, 2, 8, 16, 32  # pg_trigger.tgtype 位
    trunc = t["trg_ontology_release_no_truncate"]
    assert trunc & before and trunc & truncate and not trunc & row  # BEFORE TRUNCATE FOR EACH STATEMENT
    au = t["trg_ontology_release_append_only"]
    assert au & before and au & row and au & update and au & delete


async def test_releases_are_append_only_in_db(seeded):
    await _assert_append_only(seeded)


async def test_append_only_holds_with_fk_referencing_releases(seeded):
    """其它模块可以建指向 ontology_release(id) 的外键（retired_release_id、qa 的 release_id…）。

    有了外键也照样只追加。"""
    await seeded.execute(
        text(
            "CREATE TABLE _probe_release_ref (id bigserial PRIMARY KEY,"
            " release_id bigint NOT NULL REFERENCES ontology_release(id))"
        )
    )
    try:
        rid = await seeded.scalar(text("SELECT id FROM ontology_release WHERE ver='v2026.10.3'"))
        await seeded.execute(text("INSERT INTO _probe_release_ref (release_id) VALUES (:r)"), {"r": rid})
        sp = await seeded.begin_nested()  # 不带 CASCADE：被外键检查拒绝（同样清不掉）
        with pytest.raises(DBAPIError, match="foreign key"):
            await seeded.execute(text("TRUNCATE ontology_release"))
        await sp.rollback()
        await _assert_append_only(seeded)
        # 引用方的行也没被 CASCADE 清掉
        assert await seeded.scalar(text("SELECT count(*) FROM _probe_release_ref")) == 1
    finally:
        await seeded.execute(text("DROP TABLE IF EXISTS _probe_release_ref"))


async def test_rollback_creates_new_version_with_target_content(seeded):
    svc = VersioningService(FakeStaticProvider())
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    v4 = await svc.create_version(seeded, ["CR-0011"], published_by="U010001", note="毛利率口径", now=now)
    before = await svc.get_snapshot(seeded, v4)
    # 只能回滚最近一次发布：rollback_of 必须是当前最新版
    with pytest.raises(AppError) as e:
        await svc.create_version(
            seeded, ["CR-0015"], rollback_of="v2026.10.3", published_by="U010001", note="", now=now
        )
    assert e.value.code == "ROLLBACK_NOT_LATEST" and e.value.status_code == 409
    calls = []
    svc.provider.build_snapshot = lambda *a: calls.append(a)  # 回滚不调用任何 builder
    v5 = await svc.create_version(
        seeded, ["CR-0015"], rollback_of=v4, published_by="U010001", note="回滚 v2026.10.4", now=now
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
            seeded, ["CR-9"], rollback_of="v1999.01.1", published_by="U010001", note=""
        )
    assert e.value.code == "ROLLBACK_NOT_LATEST"


async def test_publish_uses_central_clock(seeded):
    ver = await VersioningService().create_version(seeded, ["CR-0011"], published_by="U010001", note="")
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
            publisher=await seeded.scalar(select(SysUser).where(SysUser.code == "U010002")),
            published_at=datetime(2026, 10, 25, 16, tzinfo=UTC),
        )
    )
    await seeded.flush()
    ver = await VersioningService().create_version(seeded, ["CR-0011"], published_by="U010001", note="")
    snap = await VersioningService().get_snapshot(seeded, ver)
    assert snap["rules"] == {"R-007": {"x": 1}}  # 未登记的 section 原样沿用上一版
    assert snap["objectTypes"]["order"]["applied"] == ["CR-0011"]  # 登记的 builder 组装


async def test_rollback_calls_restore_section_hooks(seeded):
    from app.modules.versioning.sections import register_section, unregister_section

    restored, ids, vers = [], [], []

    class Hooked:
        section = "rules"

        async def build_section(self, session, base, change_order_ids):
            return {"R-007": {"version": 9}}

        def seed_section(self, ver, ctx):
            return {}

        async def restore_section(self, session, content, *, release_id):
            restored.append(content)
            ids.append(release_id)
            row = await session.get(OntologyRelease, release_id)  # 新版本行已写入、同一事务里可见
            vers.append(row.ver if row else None)

    register_section(Hooked())
    try:

        class WithRules(FakeStaticProvider):
            async def build_snapshot(self, session, base_ver, change_order_ids):
                snap = await super().build_snapshot(session, base_ver, change_order_ids)
                return snap | {"rules": {"R-007": {"version": 9}}}

        svc = VersioningService(WithRules())
        now = datetime(2026, 10, 25, 12, tzinfo=UTC)
        v4 = await svc.create_version(seeded, ["CR-0011"], published_by="U010001", note="", now=now)
        v5 = await svc.create_version(seeded, ["CR-0012"], published_by="U010001", note="", now=now)
        v6 = await svc.create_version(
            seeded, ["CR-0015"], rollback_of=v5, published_by="U010001", note="", now=now
        )
        prev = await svc.get_snapshot(seeded, v4)
        assert prev["rules"] == {"R-007": {"version": 9}}
        assert restored == [prev["rules"]]
        assert ids == [(await _by_ver(seeded, v6)).id]  # 这次回滚新写入的版本行
        assert vers == [v6]
        # 回滚到没有 rules 这一节的版本（v2026.10.3 的 rules 为空）→ 不调钩子（静态裁定）
        restored.clear()
        assert (await svc.get_snapshot(seeded, "v2026.10.3")).get("rules") is None
        plain = VersioningService(FakeStaticProvider())
        v7 = await plain.create_version(seeded, ["CR-0013"], published_by="U010001", note="", now=now)
        v8 = await plain.create_version(seeded, ["CR-0014"], published_by="U010001", note="", now=now)
        assert (await plain.get_snapshot(seeded, v7)).get("rules") is None
        await plain.create_version(
            seeded, ["CR-0016"], rollback_of=v8, published_by="U010001", note="", now=now
        )
        assert restored == []
    finally:
        unregister_section("rules")


# ---- 发布人 = sys_user 外键；outbox release.published（PDE must-fix 5/6）----
async def test_publisher_is_sys_user_fk(seeded):
    r = await _by_ver(seeded, "v2026.10.3")
    assert r.publisher.code and r.published_by == r.publisher.name  # 接口 by 仍是姓名
    raw = await seeded.scalar(text("SELECT published_by_id FROM ontology_release WHERE ver='v2026.10.3'"))
    assert raw == r.publisher.id
    by_mock = {m["ver"]: m["by"] for m in MOCK}
    for rel in (await seeded.scalars(select(OntologyRelease))).all():
        assert rel.published_by == by_mock[rel.ver]
    with pytest.raises(AppError) as e:  # 发布人传姓名 / 不存在的编号 → 422
        await VersioningService().create_version(seeded, ["CR-0011"], published_by="王敏", note="")
    assert (e.value.code, e.value.status_code) == ("USER_NOT_FOUND", 422)


async def _events(session):
    from app.common.outbox import OutboxEvent

    return (await session.scalars(select(OutboxEvent).order_by(OutboxEvent.id))).all()


async def test_outbox_release_and_rollback(seeded):
    from app.common.outbox import RELEASE_PUBLISHED

    svc, now = VersioningService(), datetime(2026, 10, 25, 16, tzinfo=UTC)
    v4 = await svc.create_version(seeded, ["CR-0011", "CR-0012"], published_by="U010001", note="", now=now)
    v5 = await svc.create_version(
        seeded, ["CR-0015"], rollback_of=v4, published_by="U010001", note="", now=now
    )
    evs = await _events(seeded)
    assert [e.topic for e in evs] == [RELEASE_PUBLISHED] * 2
    assert [e.event_id for e in evs] == [f"ontology_release:{v}:release.published" for v in (v4, v5)]
    assert evs[0].payload == {
        "ver": v4,
        "parentVer": "v2026.10.3",
        "kind": "release",
        "rollbackOf": None,
        "changeOrderIds": ["CR-0011", "CR-0012"],
    }
    assert evs[1].payload == {
        "ver": v5,
        "parentVer": v4,
        "kind": "rollback",
        "rollbackOf": v4,
        "changeOrderIds": ["CR-0015"],
    }
    assert all(e.dispatched_at is None and e.attempts == 0 for e in evs)


async def test_outbox_rolls_back_with_release(seeded):
    """同一事务：发布回滚，事件一起消失。"""
    svc = VersioningService()
    sp = await seeded.begin_nested()
    await svc.create_version(seeded, ["CR-0011"], published_by="U010001", note="")
    assert len(await _events(seeded)) == 1
    await sp.rollback()
    assert await _events(seeded) == [] and await svc.current_version(seeded) == "v2026.10.3"


async def test_outbox_event_id_unique(session):
    from app.common.outbox import emit

    await emit(session, "t.x", {"a": 1}, event_id="e-1")
    sp = await session.begin_nested()
    with pytest.raises(Exception, match="uq_outbox_event_event_id"):
        await emit(session, "t.x", {"a": 2}, event_id="e-1")
    await sp.rollback()


async def test_latest_is_by_id_not_published_at(seeded):
    """后插入的版本即使 published_at 更早，也是最新（按 id desc，非时间）。"""
    from app.modules.versioning.repo import ReleaseRepo

    svc = VersioningService()
    ver = await svc.create_version(
        seeded, ["CR-0011"], published_by="U010001", note="", now=datetime(2020, 1, 1, tzinfo=UTC)
    )
    assert (await ReleaseRepo(seeded).latest()).ver == ver


async def test_restore_section_skipped_when_content_none(seeded):
    """静态裁定：被恢复版本没有这一节（None）→ 不调用钩子；空内容 {} 照样调用。"""
    from app.modules.versioning.sections import register_section, restore_sections, unregister_section

    calls = []

    class Hooked:
        def __init__(self, section):
            self.section = section

        async def build_section(self, session, base, change_order_ids):
            return base

        def seed_section(self, ver, ctx):
            return {}

        async def restore_section(self, session, content, *, release_id):
            calls.append((self.section, content, release_id))

    for name in ("qaPaths", "eventTypes", "lifecycles"):
        register_section(Hooked(name))
    try:
        await restore_sections(seeded, {"qaPaths": None, "eventTypes": {}}, 42)  # lifecycles 缺失
        assert calls == [("eventTypes", {}, 42)]
    finally:
        for name in ("qaPaths", "eventTypes", "lifecycles"):
            unregister_section(name)


async def test_release_by_is_name_and_by_id_is_user_code(client, seeded):
    """by = 姓名（前端 Release.by 直接显示）；byId = 同一外键的 sys_user.code（团队约定人员外键传 code）。"""
    code_of = {u.name: u.code for u in (await seeded.scalars(select(SysUser))).all()}
    body = (await client.get("/api/ontology/v1/releases")).json()
    assert body["items"] and all(it["byId"] == code_of[it["by"]] for it in body["items"])
    assert body["items"][0]["by"] == "王敏" and body["items"][0]["byId"] == "U010001"
    cur = (await client.get("/api/ontology/v1/releases/current")).json()["release"]
    assert (cur["by"], cur["byId"]) == ("王敏", "U010001")
    detail = (await client.get("/api/ontology/v1/releases/v2026.09.3")).json()
    assert detail["by"] == "孙浩然" and detail["byId"] == code_of["孙浩然"]
