"""通用约定：BaseRepository（软删除、乐观锁、唯一冲突）、枚举 code/标签、时间格式、幂等键。

用一张只在测试里存在的表 widget（独立 metadata，不进迁移），在事务里建表、结束回滚。
"""

from datetime import datetime

import pytest
from sqlalchemy import ForeignKey, MetaData, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.common.pagination import PageParams
from app.common.repository import BaseRepository
from app.contracts.actions import SubmitResult, SubmitStatus, idempotency_key_for, new_resubmit_key
from app.core.enums import LabelEnum
from app.core.errors import AppError, VersionConflict
from app.core.schema import ApiModel, CodeId, DateTimeMin, DateTimeSec, DisplayDateTime, parse_display_dt
from app.db.base import (
    NAMING_CONVENTION,
    CodeMixin,
    MutableTableMixin,
    SoftDeleteMixin,
    unique_except_seed,
)
from app.db.types import JsonType, enum_column


class CrStatus(LabelEnum):
    draft = "草稿"
    in_review = "审核中"
    published = "已发布"


class _TBase(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Widget(CodeMixin, MutableTableMixin, SoftDeleteMixin, _TBase):
    __tablename__ = "widget"
    __table_args__ = (unique_except_seed("widget", "idem_key"),)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    idem_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[CrStatus] = mapped_column(enum_column(CrStatus), default=CrStatus.draft)
    extra: Mapped[dict] = mapped_column(JsonType, default=dict)


class WidgetPart(CodeMixin, MutableTableMixin, _TBase):
    """外键指向代理键 id；seed 里写的是 widget 的编号，由 SeedSpec.refs 换成 id。"""

    __tablename__ = "widget_part"
    widget_id: Mapped[int] = mapped_column(ForeignKey("widget.id"), nullable=False)


class WidgetOut(ApiModel):
    id: CodeId
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
    w = await repo.create(Widget(code="W-1", name="甲"), actor="王敏")
    assert w.created_by == "王敏" and w.lock_version == 1
    await repo.create(Widget(code="W-2", name="乙"), actor="王敏")
    rows, total = await repo.list_page(repo.base_query().order_by(Widget.id), PageParams(page=1, size=1))
    assert total == 2 and [r.code for r in rows] == ["W-1"] and isinstance(rows[0].id, int)
    await repo.soft_delete(w, actor="刘婷")
    assert await repo.get_by_code("W-1") is None and await repo.get(w.id) is None
    _, total = await repo.list_page(repo.base_query(), PageParams())
    assert total == 1
    with pytest.raises(AppError) as e:
        await repo.get_by_code_or_404("W-1")
    assert e.value.code == "NOT_FOUND"


async def test_lock_version(repo):
    w = await repo.create(Widget(code="W-1", name="甲"))
    await repo.update(w, {"status": CrStatus.in_review}, actor="何静", lock_version=1)
    assert w.lock_version == 2
    with pytest.raises(VersionConflict):
        await repo.update(w, {"status": CrStatus.published}, lock_version=1)


async def test_duplicate_key(repo, session):
    await repo.create(Widget(code="W-1", name="甲"))
    async with session.begin_nested():
        with pytest.raises(AppError) as e:
            await repo.create(Widget(code="W-2", name="甲"))
    assert e.value.code == "DUPLICATE_KEY"


async def test_enum_code_in_db_label_in_api(repo, session):
    w = await repo.create(Widget(code="W-1", name="甲", status=CrStatus.in_review))
    raw = (await session.execute(select(Widget.__table__.c.status).where(Widget.code == "W-1"))).scalar()
    assert raw == CrStatus.in_review  # ORM 读回枚举
    db_text = await session.scalar(
        Widget.__table__.select().with_only_columns(Widget.__table__.c.status.cast(String))
    )
    assert db_text == "in_review"  # 库里存英文 code
    out = WidgetOut.model_validate(w).model_dump(mode="json", by_alias=True)
    assert out["id"] == "W-1"  # 接口 id = 业务编号，不是代理键
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


def test_datetime_per_field_formats():
    class M(ApiModel):
        at: DateTimeMin
        sec: DateTimeSec

    m = M.model_validate({"at": "2026-10-24 21:17", "sec": "2026-10-25 13:32:06"})
    assert m.model_dump(mode="json") == {"at": "2026-10-24 21:17", "sec": "2026-10-25 13:32:06"}
    assert M.model_validate({"at": "2026-10-24 21:17:59", "sec": "2026-10-25 13:32"}).model_dump(
        mode="json"
    ) == {
        "at": "2026-10-24 21:17",
        "sec": "2026-10-25 13:32:00",
    }
    assert parse_display_dt("2026-10-25 13:32:06").second == 6


def test_timeline_dates_fill_and_roll_over():
    from app.seeds import fill_timeline_dates

    ts = fill_timeline_dates(
        [{"time": "2026-10-24 23:59:58"}, {"time": "23:59:59"}, {"time": "00:00:03"}, {"time": "00:01"}]
    )
    assert [t.strftime("%m-%d %H:%M:%S") for t in ts] == [
        "10-24 23:59:58",
        "10-24 23:59:59",
        "10-25 00:00:03",
        "10-25 00:01:00",
    ]
    with pytest.raises(ValueError):
        fill_timeline_dates([{"time": "13:00:00"}])


def test_idempotency_keys():
    from app.contracts.actions import ReconcileOutcome, split_retry_suffix

    k = idempotency_key_for("ACT-20261025-3834")
    assert k == "act-20261025-3834"  # 和 export-mock runsOf 的 idemKey 一致
    assert new_resubmit_key(k) == f"{k}-r1"
    assert new_resubmit_key(f"{k}-r1") == f"{k}-r2"
    assert split_retry_suffix(f"{k}-r2") == (k, 2) and split_retry_suffix(k) == (k, 0)
    assert SubmitResult(SubmitStatus.UNKNOWN).receipt_id is None
    assert [o.label for o in ReconcileOutcome] == ["外部已成功", "外部未执行"]
    assert not ReconcileOutcome.not_executed.resubmittable


def test_action_contract_labels():
    from app.contracts.actions import ExtStatus, SubmitStatus

    assert [s.label for s in SubmitStatus] == ["成功", "失败", "结果未知"]
    assert [s.value for s in ExtStatus] == ["查到·成功", "查到·未执行", "查无"]
    assert ExtStatus.found_succeeded.outcome == "succeeded" and ExtStatus.not_found.outcome is None


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

    (tmp_path / "json" / "common").mkdir(parents=True)
    (tmp_path / "extra" / "common").mkdir(parents=True)
    mock = [
        {"id": "W-1", "name": "甲", "key": "k1"},
        {"id": "W-2", "name": "乙", "key": "k1"},
    ]  # mock 自带重复键
    (tmp_path / "json" / "common" / "w.json").write_text(json.dumps(mock, ensure_ascii=False))
    (tmp_path / "extra" / "common" / "w.json").write_text(
        json.dumps([{"id": "W-9", "name": "补", "key": None}])
    )
    t = lambda r, _: {"code": r["id"], "name": r["name"], "idem_key": r["key"]}  # noqa: E731
    mod = types.ModuleType("app.modules.tstmod.seeds")
    mod.JSON_MODULE = "common"
    mod.SEEDS = [
        SeedSpec(Widget, "w.json", key=("code",), transform=t, source="extra"),
        SeedSpec(Widget, "w.json", key=("code",), transform=t),
    ]
    monkeypatch.setitem(sys.modules, "app.modules.tstmod.seeds", mod)

    for _ in range(2):  # 幂等
        reports = await run_seeds(
            session, ["tstmod"], json_root=tmp_path / "json", extra_root=tmp_path / "extra"
        )
    assert [r.file for r in reports] == ["json/common/w.json", "extra/common/w.json"]  # 先 json 再 extra
    rows = (await session.scalars(select(Widget).order_by(Widget.id))).all()
    assert [(w.code, w.is_seed) for w in rows] == [("W-1", True), ("W-2", True), ("W-9", True)]
    # 业务新写入的行仍受唯一约束
    await repo.create(Widget(code="W-3", name="丙", idem_key="k1"))  # 和 seed 重复：允许
    async with session.begin_nested():
        with pytest.raises(AppError) as e:
            await repo.create(Widget(code="W-4", name="丁", idem_key="k1"))
    assert e.value.code == "DUPLICATE_KEY"


async def test_seed_resolves_codes_to_surrogate_ids(repo, session, tmp_path, monkeypatch):
    import json
    import sys
    import types

    from app.seeds import Ref, SeedSpec
    from app.seeds.loader import run_seeds

    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "w.json").write_text(json.dumps([{"id": "W-1", "name": "甲"}]))
    (tmp_path / "common" / "p.json").write_text(json.dumps([{"id": "P-1", "widget": "W-1"}]))
    mod = types.ModuleType("app.modules.tstref.seeds")
    mod.JSON_MODULE = "common"
    mod.SEEDS = [
        SeedSpec(Widget, "w.json", ("code",), transform=lambda r, _: {"code": r["id"], "name": r["name"]}),
        SeedSpec(
            WidgetPart,
            "p.json",
            ("code",),
            transform=lambda r, _: {"code": r["id"], "widget_id": r["widget"]},
            refs={"widget_id": Ref(Widget)},
        ),
    ]
    monkeypatch.setitem(sys.modules, "app.modules.tstref.seeds", mod)
    await run_seeds(session, ["tstref"], json_root=tmp_path)
    w = await repo.get_by_code("W-1")
    part = (await session.scalars(select(WidgetPart))).one()
    assert part.widget_id == w.id and isinstance(w.id, int)

    (tmp_path / "common" / "p.json").write_text(json.dumps([{"id": "P-2", "widget": "W-404"}]))
    with pytest.raises(ValueError, match="W-404"):
        await run_seeds(session, ["tstref"], json_root=tmp_path)


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
    assert "widget.name = %(name_1)s" in sql and "IN (__[POSTCOMPILE_idem_key_1])" in sql
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
    assert combine_or([one, None]) is None  # 某角色明确配置为「全部」→ 不限制
    assert combine_or([]) == {"deny": True}  # 没有任何角色范围 → 拒绝
    assert _sql(combine_or([])) == "false"


def test_row_filter_value_shapes_rejected_at_schema():
    from pydantic import ValidationError

    from app.common.row_filter import parse_row_filter

    for bad in (
        {"field": "name", "op": "=", "value": ["a", "b"]},
        {"field": "name", "op": "like", "value": ["a%"]},
        {"field": "name", "op": "like", "value": 3},
        {"field": "a.b.c", "op": "=", "value": "x"},  # 多跳
    ):
        with pytest.raises(ValidationError):
            parse_row_filter(bad)
    with pytest.raises(AppError) as e:  # 多值用户属性不能用 like
        _sql({"field": "name", "op": "like", "valueFrom": "user.regions"}, {"regions": ["华东"]})
    assert e.value.code == "BAD_ROW_FILTER"
    assert "IN" in _sql(
        {"field": "name", "op": "=", "valueFrom": "user.regions"}, {"regions": ["华东", "华南"]}
    )


def _link_tables():
    from sqlalchemy import Column, Table

    from app.common.row_filter import ObjectTable

    md = MetaData()
    order = Table("biz_order", md, Column("order_id", String), Column("store_id", String))
    store = Table("store", md, Column("store_id", String), Column("region", String), Column("secret", String))
    return {
        "order": ObjectTable(order, {"order_id": order.c.order_id, "store_id": order.c.store_id}),
        "store": ObjectTable(store, {"store_id": store.c.store_id, "region": store.c.region}),
    }


LINKS = [  # 静态关系定义（export-mock static/LINKS.json 的字段）
    {
        "id": "order_belongs_to_store",
        "from": "order",
        "to": "store",
        "fromProp": "store_id",
        "toProp": "store_id",
    },
    {
        "id": "order_has_lines",
        "from": "order",
        "to": "order_line",
        "fromProp": "order_id",
        "toProp": "order_id",
    },
    {"id": "store_in_region", "from": "store", "to": "region", "fromProp": "region", "toProp": "id"},
]


def test_row_filter_one_hop_from_static_links():
    from sqlalchemy.dialects import postgresql

    from app.common.row_filter import link_paths_from_definitions, to_sql

    objs = _link_tables()
    links = link_paths_from_definitions("order", LINKS, objs)
    assert set(links) == {"order_belongs_to_store"}  # 只有 from=order 且两端都登记了表的关系
    expr = {"field": "order_belongs_to_store.region", "op": "=", "valueFrom": "user.region"}
    sql = str(
        to_sql(expr, objs["order"].columns, {"region": "华东"}, links).compile(dialect=postgresql.dialect())
    )
    assert sql.startswith("EXISTS (SELECT 1") and "store.store_id = biz_order.store_id" in sql
    assert "store.region = %(region_1)s" in sql and "华东" not in sql  # 绑定参数
    for field in ("undeclared_link.region", "order_belongs_to_store.secret"):
        with pytest.raises(AppError) as e:
            to_sql({"field": field, "op": "=", "value": "x"}, objs["order"].columns, {}, links)
        assert e.value.code == "BAD_ROW_FILTER"
    with pytest.raises(AppError):  # 没传 links → 不允许跨对象
        to_sql(expr, objs["order"].columns, {"region": "华东"})


def test_state_machine():
    from app.core.state import InvalidTransition, ensure_transition

    table = {"draft": {"in_review"}, "in_review": {"approved", "draft"}}
    ensure_transition(table, "draft", "in_review")
    with pytest.raises(InvalidTransition) as e:
        ensure_transition(table, "draft", "approved")
    assert e.value.code == "STATE_INVALID" and e.value.status_code == 409


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
        "release.after_commit",
        "action.reconcile",
        "export.run",
        "admin.expire_api_keys",
        "admin.cleanup_audit",
    }
    assert EXTRA_RECONCILE_ROUNDS == 3
    by = {j.name: j for j in JOBS}
    assert by["action.reconcile"].cron == "*/15 * * * *" and by["action.reconcile"].owner == "integration"
    assert not by["admin.cleanup_audit"].enabled and by["admin.cleanup_audit"].cron is None


def test_procrastinate_worker_registers_all_jobs():
    from app.jobs import JOBS
    from app.jobs.worker import app as worker

    assert {j.name for j in JOBS} <= set(worker.tasks)
    periodic = {p.periodic_id for p in worker.periodic_registry.periodic_tasks.values()}
    assert periodic == {j.name for j in JOBS if j.cron and j.enabled}
    assert "admin.cleanup_audit" not in periodic


async def test_apply_row_filter_injection(monkeypatch):
    from sqlalchemy.dialects import postgresql

    import app.common.auth as auth
    from app.common.row_filter import apply_row_filter

    user = await auth.get_current_user()
    stmt = select(Widget)
    assert await apply_row_filter(stmt, user, "widget", {}) is stmt  # 桩：不限制

    async def none_roles(_user, _obj):
        from app.common.row_filter import combine_or

        return combine_or([])

    monkeypatch.setattr(auth, "row_filter", none_roles)
    denied = await apply_row_filter(stmt, user, "widget", {})
    assert "WHERE false" in str(denied.compile(dialect=postgresql.dialect()))

    async def fake(_user, _obj):
        return {"field": "name", "op": "in", "value": ["甲"]}

    monkeypatch.setattr(auth, "row_filter", fake)
    out = await apply_row_filter(stmt, user, "widget", {"name": Widget.__table__.c.name})
    assert "WHERE widget.name IN" in str(out.compile(dialect=postgresql.dialect()))


# ---- seed：派生文件展开 / 分组 / 批量 upsert ----
FIXTURE = __import__("pathlib").Path(__file__).parent / "fixtures" / "export"


def test_derived_expansion_from_export_fixture():
    from app.seeds import SeedContext, derived_args, derived_rows, resolve_arg

    ctx = SeedContext(FIXTURE / "dynamic", root=FIXTURE)
    rows = list(derived_rows(ctx.load("derived/runsOf.json", group="action")))  # 跨组读
    assert len(rows) == 4 and derived_args(rows[0]) == ["issue_recall_coupon"]
    assert rows[0]["idemKey"] == idempotency_key_for(rows[0]["id"])
    ds = list(derived_rows(ctx.load("derived/dsTables.json", group="system")))
    assert derived_args(ds[0]) == ["DS-001"]  # {"ref","key","id"} 引用解析成 id
    assert resolve_arg({"ref": "x", "key": "id", "id": "A", "field": "name", "name": "甲"}) == "甲"


def test_spec_group_rules():
    from app.seeds import MODULE_GROUP, SeedSpec
    from app.seeds.loader import module_group, spec_group

    assert (
        {m: module_group(m) for m in ("admin", "data", "integration", "event", "biz", "versioning")}
        == {
            "admin": "system",
            "data": "system",
            "integration": "system",
            "event": "action",
            "biz": "common",
            "versioning": "static",
        }
        == {m: MODULE_GROUP[m] for m in ("admin", "data", "integration", "event", "biz", "versioning")}
    )
    with pytest.raises(ValueError, match="system/"):  # dsTables 以 system 组为准
        spec_group("static", SeedSpec(Widget, "derived/dsTables.json", ("id",)))
    assert spec_group("data", SeedSpec(Widget, "derived/dsTables.json", ("id",))) == "system"
    assert SeedSpec(Widget, "derived/runsOf.json", ("id",), group="action").is_derived


def test_upsert_batching_helpers():
    from app.db.dialects.postgres import PG_MAX_PARAMS, batch_size, chunked, dedupe_by_key

    assert batch_size(10) == PG_MAX_PARAMS // 10 == 3276
    rows = [{"id": i % 5, "v": i} for i in range(12)]
    d = dedupe_by_key(rows, ["id"])
    assert len(d) == 5 and {r["id"]: r["v"] for r in d}[0] == 10  # 后出现的覆盖
    assert [len(c) for c in chunked(list(range(7)), 3)] == [3, 3, 1]


async def test_upsert_many_rows_in_batches(repo, session):
    from app.db.dialects import get_adapter

    rows = [{"code": f"W-{i % 9000}", "name": f"n{i % 9000}", "status": CrStatus.draft} for i in range(12000)]
    n = await get_adapter(session).upsert(session, Widget, rows, ["code"])  # 3 列 → 每批 10922 行，且有重复键
    assert n == 9000
    assert await session.scalar(select(__import__("sqlalchemy").func.count()).select_from(Widget)) == 9000


async def test_commit_failure_returns_500_with_request_id():
    """SessionDep scope="function"：提交在发送响应前完成，提交失败 → 500（不会先回 200）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.core.errors import install_error_handlers
    from app.core.request_id import RequestIdMiddleware
    from app.db.session import SessionDep, get_session

    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    class _S:
        async def commit(self):
            raise RuntimeError("commit failed")

        async def rollback(self):
            pass

    async def _failing():
        s = _S()
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise

    @app.post("/w")
    async def w(session: SessionDep) -> dict:
        return {"ok": True}

    app.dependency_overrides[get_session] = _failing
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.post("/w", headers={"X-Request-ID": "rid-c"})
    assert r.status_code == 500 and r.json()["requestId"] == "rid-c" and r.headers["x-request-id"] == "rid-c"


def test_common_409_codes():
    from app.core.errors import DuplicateKey, EmptyInput, EmptyRelease, Forbidden

    pairs = ((DuplicateKey, "DUPLICATE_KEY"), (EmptyInput, "EMPTY_INPUT"), (EmptyRelease, "EMPTY_RELEASE"))
    for cls, code in pairs:
        e = cls()
        assert (e.status_code, e.code) == (409, code)
    assert (Forbidden().status_code, Forbidden().code) == (403, "NO_PERMISSION")
