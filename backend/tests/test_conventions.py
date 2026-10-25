"""通用约定：BaseRepository（软删除、乐观锁、唯一冲突）、枚举 code/标签、时间格式、幂等键。

用一张只在测试里存在的表 tst_widget（独立 metadata，不进迁移），在事务里建表、结束回滚。
"""

from datetime import datetime

import pytest
from sqlalchemy import MetaData, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.pagination import PageParams
from app.common.repository import BaseRepository
from app.contracts.actions import SubmitResult, SubmitStatus, new_idempotency_key, new_resubmit_key
from app.core.enums import LabelEnum
from app.core.errors import AppError, VersionConflict
from app.core.schema import ApiModel, DisplayDateTime, parse_display_dt
from app.db.base import (
    NAMING_CONVENTION,
    AuditColumnsMixin,
    LockVersionMixin,
    SeedMarkMixin,
    SoftDeleteMixin,
    StrIdMixin,
    unique_except_seed,
)
from app.db.types import JsonType, enum_column


class CrStatus(LabelEnum):
    draft = "草稿"
    in_review = "审核中"
    published = "已发布"


class _TBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Widget(StrIdMixin, AuditColumnsMixin, SoftDeleteMixin, LockVersionMixin, SeedMarkMixin, _TBase):
    __tablename__ = "tst_widget"
    __table_args__ = (unique_except_seed("tst_widget", "idem_key"),)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    idem_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[CrStatus] = mapped_column(enum_column(CrStatus), default=CrStatus.draft)
    extra: Mapped[dict] = mapped_column(JsonType, default=dict)


class WidgetOut(ApiModel):
    id: str
    name: str
    status: CrStatus
    lock_version: int
    updated_at: DisplayDateTime


class WidgetRepo(BaseRepository[Widget]):
    model = Widget


@pytest.fixture
async def repo(session, conn):
    await conn.run_sync(_TBase.metadata.create_all)
    return WidgetRepo(session)


async def test_crud_soft_delete(repo, session):
    w = await repo.create(Widget(id="W-1", name="甲"), actor="王敏")
    assert w.created_by == "王敏" and w.lock_version == 1
    await repo.create(Widget(id="W-2", name="乙"), actor="王敏")
    rows, total = await repo.list_page(repo.base_query().order_by(Widget.id), PageParams(page=1, size=1))
    assert total == 2 and [r.id for r in rows] == ["W-1"]
    await repo.soft_delete(w, actor="刘婷")
    assert await repo.get("W-1") is None
    _, total = await repo.list_page(repo.base_query(), PageParams())
    assert total == 1
    with pytest.raises(AppError) as e:
        await repo.get_or_404("W-1")
    assert e.value.code == "NOT_FOUND"


async def test_lock_version(repo):
    w = await repo.create(Widget(id="W-1", name="甲"))
    await repo.update(w, {"status": CrStatus.in_review}, actor="何静", lock_version=1)
    assert w.lock_version == 2 and w.updated_by == "何静"
    with pytest.raises(VersionConflict):
        await repo.update(w, {"status": CrStatus.published}, lock_version=1)


async def test_duplicate_key(repo, session):
    await repo.create(Widget(id="W-1", name="甲"))
    async with session.begin_nested():
        with pytest.raises(AppError) as e:
            await repo.create(Widget(id="W-2", name="甲"))
    assert e.value.code == "DUPLICATE_KEY"


async def test_enum_code_in_db_label_in_api(repo, session):
    w = await repo.create(Widget(id="W-1", name="甲", status=CrStatus.in_review))
    raw = (await session.execute(select(Widget.__table__.c.status).where(Widget.id == "W-1"))).scalar()
    assert raw == CrStatus.in_review  # ORM 读回枚举
    db_text = await session.scalar(
        Widget.__table__.select().with_only_columns(Widget.__table__.c.status.cast(String))
    )
    assert db_text == "in_review"  # 库里存英文 code
    out = WidgetOut.model_validate(w).model_dump(mode="json", by_alias=True)
    assert out["status"] == "审核中" and out["lockVersion"] == 1  # 接口输出中文标签 + camelCase
    assert out["updatedAt"] == "2026-10-25 15:42"  # 审计列来自统一时钟


def test_display_datetime_roundtrip():
    class M(ApiModel):
        at: DisplayDateTime

    m = M.model_validate({"at": "2026-10-24 21:17"})
    assert m.at == parse_display_dt("2026-10-24 21:17")
    assert m.model_dump(mode="json") == {"at": "2026-10-24 21:17"}
    iso = M.model_validate({"at": "2026-10-24T13:17:00Z"})  # ISO 入参也接受，按北京时间输出
    assert iso.model_dump(mode="json") == {"at": "2026-10-24 21:17"}
    assert isinstance(m.at, datetime) and m.at.utcoffset().total_seconds() == 8 * 3600


def test_idempotency_keys():
    k = new_idempotency_key("replenish")
    assert k.startswith("replenish-") and len(k) == len("replenish-") + 12
    assert new_resubmit_key(k, 1) == f"{k}-r1"
    assert new_resubmit_key(f"{k}-r1", 2) == f"{k}-r2"
    assert SubmitResult(SubmitStatus.UNKNOWN).receipt_id is None


def test_action_contract_labels():
    from app.contracts.actions import ExtStatus, SubmitStatus

    assert [s.label for s in SubmitStatus] == ["成功", "失败", "结果未知"]
    assert ExtStatus.unsupported == "不支持查询"


def test_enum_mapping_helper():
    from app.core.enums import enum_map, from_api

    class JobState(LabelEnum):
        processing = "doing"
        finished = "done"

    assert enum_map(JobState) == {"processing": "doing", "finished": "done"}
    assert from_api(JobState, "doing").code == "processing"


def test_clock():
    from datetime import UTC, date

    from app.core import clock

    assert clock.local_now().strftime("%Y-%m-%d %H:%M") == "2026-10-25 15:42"
    assert clock.today() == date(2026, 10, 25)
    assert clock.last_days(7) == (date(2026, 10, 19), date(2026, 10, 25))
    with clock.frozen(datetime(2026, 11, 1, tzinfo=UTC)):
        assert clock.today() == date(2026, 11, 1)
    assert clock.today() == date(2026, 10, 25)


# ---- seed：is_seed、部分唯一索引、json → extra ----
async def test_seed_marks_and_partial_unique(repo, session, tmp_path, monkeypatch):
    import json
    import sys
    import types

    from app.seeds import SeedSpec
    from app.seeds.loader import run_seeds

    (tmp_path / "json" / "tstmod").mkdir(parents=True)
    (tmp_path / "extra" / "tstmod").mkdir(parents=True)
    mock = [
        {"id": "W-1", "name": "甲", "key": "k1"},
        {"id": "W-2", "name": "乙", "key": "k1"},
    ]  # mock 自带重复键
    (tmp_path / "json" / "tstmod" / "w.json").write_text(json.dumps(mock, ensure_ascii=False))
    (tmp_path / "extra" / "tstmod" / "w.json").write_text(
        json.dumps([{"id": "W-9", "name": "补", "key": None}])
    )
    t = lambda r, _: {"id": r["id"], "name": r["name"], "idem_key": r["key"]}  # noqa: E731
    mod = types.ModuleType("app.modules.tstmod.seeds")
    mod.SEEDS = [
        SeedSpec(Widget, "w.json", key=("id",), transform=t, source="extra"),
        SeedSpec(Widget, "w.json", key=("id",), transform=t),
    ]
    monkeypatch.setitem(sys.modules, "app.modules.tstmod.seeds", mod)

    for _ in range(2):  # 幂等
        reports = await run_seeds(
            session, ["tstmod"], json_root=tmp_path / "json", extra_root=tmp_path / "extra"
        )
    assert [r.file for r in reports] == ["json/w.json", "extra/w.json"]  # 先 json 再 extra
    rows = (await session.scalars(select(Widget).order_by(Widget.id))).all()
    assert [(w.id, w.is_seed) for w in rows] == [("W-1", True), ("W-2", True), ("W-9", True)]
    # 业务新写入的行仍受唯一约束
    await repo.create(Widget(id="W-3", name="丙", idem_key="k1"))  # 和 seed 重复：允许
    async with session.begin_nested():
        with pytest.raises(AppError) as e:
            await repo.create(Widget(id="W-4", name="丁", idem_key="k1"))
    assert e.value.code == "DUPLICATE_KEY"


# ---- 行级过滤表达式 ----
def _cols():
    t = Widget.__table__.c
    return {"name": t.name, "status": t.status, "idem_key": t.idem_key}


def _sql(expr, attrs=None) -> str:
    from sqlalchemy.dialects import postgresql

    from app.common.row_filter import to_sql

    return str(to_sql(expr, _cols(), attrs or {}).compile(dialect=postgresql.dialect()))


def test_row_filter_schema_and_sql():
    from app.common.row_filter import parse_row_filter

    expr = {
        "and": [
            {"field": "name", "op": "=", "valueFrom": "user.region"},
            {
                "or": [
                    {"field": "idem_key", "op": "∈", "value": ["a", "b"]},
                    {"field": "idem_key", "op": "is_null"},
                ]
            },
        ]
    }
    assert parse_row_filter(expr) is not None
    sql = _sql(expr, {"region": "华东"})
    assert "tst_widget.name = %(name_1)s" in sql and "IN (__[POSTCOMPILE_idem_key_1])" in sql
    assert "IS NULL" in sql and "华东" not in sql  # 值走绑定参数
    assert _sql(None) == "true"
    assert _sql({"field": "name", "op": "=", "valueFrom": "user.region"}) == "false"  # 缺用户属性 → 看不到


def test_row_filter_rejects_bad_input():
    from pydantic import ValidationError

    from app.common.row_filter import parse_row_filter

    with pytest.raises(AppError):  # 字段不在白名单
        _sql({"field": "secret", "op": "=", "value": "x"})
    for bad in (
        {"field": "name; drop table x", "op": "=", "value": "x"},
        {"field": "name", "op": "=", "value": "x", "valueFrom": "user.a"},
        {"field": "name", "op": "in", "value": "x"},
        {"field": "name", "op": "=", "valueFrom": "env.HOME"},
    ):
        with pytest.raises(ValidationError):
            parse_row_filter(bad)


def test_row_scope_from_frontend_mock():
    from app.common.row_filter import combine_or, from_row_scope

    one = from_row_scope(
        {"objType": "store", "prop": "大区", "op": "=", "values": ["华东"]}, {"大区": "region"}
    )
    many = from_row_scope({"objType": "store", "prop": "region", "op": "∈", "values": ["华东", "华南"]})
    assert one == {"field": "region", "op": "=", "value": "华东"}
    assert many == {"field": "region", "op": "in", "value": ["华东", "华南"]}
    assert combine_or([one, many]) == {"or": [one, many]}
    assert combine_or([one, None]) is None  # 任一角色不限制 → 不限制


def test_state_machine():
    from app.core.state import InvalidTransition, ensure_transition

    table = {"draft": {"in_review"}, "in_review": {"approved", "draft"}}
    ensure_transition(table, "draft", "in_review")
    with pytest.raises(InvalidTransition) as e:
        ensure_transition(table, "draft", "approved")
    assert e.value.code == "INVALID_TRANSITION" and e.value.status_code == 409


# ---- 团队决定：身份 / 租户 / 预览 / 测试连接 / 任务 / 行级注入 ----
async def test_demo_as_and_tenant_header():
    from app.common.auth import get_current_user
    from app.core.config import get_settings

    u = await get_current_user(x_demo_as="李雷", x_tenant_id="t1")
    assert (u.id, u.tenant_id) == ("李雷", "t1")
    assert (await get_current_user()).id == get_settings().dev_user


async def test_one_preview_session_per_user():
    from app.contracts.preview import InMemoryPreviewSessions, PreviewSessions

    s = InMemoryPreviewSessions()
    assert isinstance(s, PreviewSessions)
    await s.start("王敏", "李雷")
    await s.start("王敏", "韩梅梅")  # 替换，不并存
    assert (await s.current("王敏")).preview_as == "韩梅梅"
    await s.end("王敏")
    assert await s.current("王敏") is None


async def test_mock_connection_tester():
    from app.modules.data.adapters import ConnectionTester, MockConnectionTester, get_connection_tester

    t = get_connection_tester()
    assert isinstance(t, MockConnectionTester) and isinstance(t, ConnectionTester)
    assert (await t.test("postgres", {})).ok
    assert not (await t.test("postgres", {"mockFail": True})).ok


def test_job_stubs_registered():
    from app.jobs import EXTRA_RECONCILE_ROUNDS, JOBS

    names = {j.name for j in JOBS}
    assert names == {
        "event.deliver",
        "event.redeliver_failed",
        "outbox.dispatch",
        "release.expand",
        "action.reconcile",
        "export.run",
        "admin.expire_api_keys",
        "admin.cleanup_audit",
    }
    assert EXTRA_RECONCILE_ROUNDS == 3


async def test_apply_row_filter_injection(monkeypatch):
    from sqlalchemy.dialects import postgresql

    import app.common.auth as auth
    from app.common.row_filter import apply_row_filter

    user = await auth.get_current_user()
    stmt = select(Widget)
    assert await apply_row_filter(stmt, user, "widget", {}) is stmt  # 桩：不限制

    async def fake(_user, _obj):
        return {"field": "name", "op": "in", "value": ["甲"]}

    monkeypatch.setattr(auth, "row_filter", fake)
    out = await apply_row_filter(stmt, user, "widget", {"name": Widget.__table__.c.name})
    assert "WHERE tst_widget.name IN" in str(out.compile(dialect=postgresql.dialect()))
