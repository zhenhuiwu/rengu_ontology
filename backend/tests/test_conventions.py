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


def test_page_clamp_matches_frontend_paginate():
    from app.common.pagination import Page, page_count

    def front(page: int, size: int, total: int) -> int:  # src/mock/paginate.ts:12-13
        return min(max(1, page), max(1, -(-total // size)))

    for total in (0, 1, 19, 20, 21, 100):
        for page in (1, 2, 3, 6, 99):
            for size in (1, 20, 50):
                p = PageParams(page=page, size=size).clamp(total)
                assert (p.page, p.size) == (front(page, size, total), size), (page, size, total)
                assert p.offset == (p.page - 1) * size
    assert page_count(0, 20) == 1 and page_count(41, 20) == 3
    body = Page[int].of([], 0, PageParams(page=3, size=20))
    assert (body.page, body.total, body.items) == (1, 0, [])
    assert Page[int].of([1], 41, PageParams(page=9, size=20)).page == 3


def test_page_zero_and_negative_are_page_one():
    """前端 paginate.ts:13 `Math.max(1, q.page)`：0 和负数都当第 1 页，total=0 也是第 1 页。"""
    from pydantic import ValidationError

    from app.common.pagination import Page

    for page in (0, -3):
        p = PageParams(page=page, size=20)
        assert p.page == 1 and p.offset == 0
        for total in (0, 45):
            c = p.clamp(total)
            assert (c.page, c.offset) == (1, 0), (page, total)
        assert Page[int].of([1, 2], 45, PageParams(page=page, size=20)).page == 1
        body = Page[int].of([], 0, PageParams(page=page, size=20))
        assert (body.page, body.total, body.items) == (1, 0, [])
    for size in (0, 201):
        with pytest.raises(ValidationError):
            PageParams(size=size)


async def test_list_page_zero_and_negative(repo):
    for code in ("W-1", "W-2", "W-3"):
        await repo.create(Widget(code=code, name=code), actor="王敏")
    for page in (0, -3):
        rows, total = await repo.list_page(
            repo.base_query().order_by(Widget.id), PageParams(page=page, size=2)
        )
        assert total == 3 and [r.code for r in rows] == ["W-1", "W-2"], page
        rows, total = await repo.list_page(
            repo.base_query().where(Widget.code == "无"), PageParams(page=page, size=2)
        )
        assert (rows, total) == ([], 0), page


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
    # 页码越界收拢到最后一页（前端 paginate.ts:13）
    rows, total = await repo.list_page(repo.base_query(), PageParams(page=7, size=1))
    assert total == 1 and [r.code for r in rows] == ["W-2"]
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
async def test_demo_as_and_tenant_header(users):
    """X-Demo-As / RENGU_DEV_USER 传用户编号（sys_user.code），按编号解析出姓名。"""
    from app.common.auth import get_current_user
    from app.core.config import get_settings
    from app.core.errors import Unauthorized

    u = await get_current_user(users, x_demo_as="U010231", x_tenant_id="t1")
    assert (u.id, u.name, u.tenant_id, u.is_ontology_admin) == ("U010231", "苏刚", "t1", False)
    me = await get_current_user(users)
    assert (
        (me.id, me.name, me.is_ontology_admin)
        == (get_settings().dev_user, "王敏", True)
        == ("U010001", "王敏", True)
    )
    li = await get_current_user(users, x_demo_as="U010006")
    assert (li.name, li.is_ontology_admin) == ("李娜", False)
    for bad in ("王敏", "U999999"):  # 传姓名 / 不存在的编号 → 401
        with pytest.raises(Unauthorized):
            await get_current_user(users, x_demo_as=bad)


async def test_demo_as_header_over_http(client, users):
    from app.common.auth import CurrentUserDep
    from app.main import app

    @app.get("/__test/me")
    async def me(user: CurrentUserDep) -> dict:
        return {"id": user.id, "name": user.name}

    try:
        assert (await client.get("/__test/me")).json() == {"id": "U010001", "name": "王敏"}
        assert (await client.get("/__test/me", headers={"X-Demo-As": "U010006"})).json()["name"] == "李娜"
        # 姓名本来也发不出去（HTTP 头只能 ASCII），不存在的编号 → 401
        r = await client.get("/__test/me", headers={"X-Demo-As": "U999999"})
        assert (r.status_code, r.json()["code"]) == (401, "UNAUTHORIZED")
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/__test/me"]


async def test_prod_ignores_demo_as(client, users, monkeypatch):
    """prod 不认 X-Demo-As / RENGU_DEV_USER：传管理员编号也是 401（真实鉴权接入前一律拒绝）。"""
    from app.common.auth import CurrentUserDep, get_current_user
    from app.core.config import get_settings
    from app.core.errors import Unauthorized
    from app.main import app

    @app.get("/__test/me")
    async def me(user: CurrentUserDep) -> dict:
        return {"id": user.id}

    monkeypatch.setenv("RENGU_ENV", "prod")
    get_settings.cache_clear()
    try:
        assert get_settings().env == "prod"
        with pytest.raises(Unauthorized):
            await get_current_user(users, x_demo_as="U010001")
        for h in ({"X-Demo-As": "U010001"}, {}):
            r = await client.get("/__test/me", headers=h)
            assert (r.status_code, r.json()["code"]) == (401, "UNAUTHORIZED")
    finally:
        monkeypatch.setenv("RENGU_ENV", "test")
        get_settings.cache_clear()
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/__test/me"]
    assert get_settings().env == "test"


async def test_seed_personas_and_unique_names(users, session, monkeypatch):
    """SYS_USERS 补的演示人物；seed 的 sys_user 姓名必须唯一（动态按姓名比较「本人」），重名整批不写。"""
    import dataclasses

    from sqlalchemy import func, update

    from app.common.auth import get_current_user
    from app.modules.admin import seeds as admin_seeds
    from app.modules.admin.models import SysUser
    from app.seeds.loader import module_checks, run_seeds

    people = (
        ("U010005", "孙浩然"),
        ("U010006", "李娜"),
        ("U010007", "赵蕾"),
        ("U010008", "吴佳"),
        ("U010231", "苏刚"),
    )
    for code, name in people:  # 苏刚（?previewAs= 默认预览对象）来自 ADMIN_USERS.json
        u = await get_current_user(users, x_demo_as=code)
        assert (u.name, u.is_ontology_admin) == (name, False)
    # alert_assignable 不由 admin seed 写（动态的 seed 按姓名回填）
    assert await users.scalar(select(func.count()).where(SysUser.alert_assignable.is_not(None))) == 0
    names = (await users.execute(select(SysUser.name))).scalars().all()
    assert len(names) == len(set(names))

    assert admin_seeds.check_unique_names in module_checks("admin")
    admin_seeds.check_unique_names({"sys_user": [{"code": "A", "name": "甲"}, {"code": "B", "name": "乙"}]})
    with pytest.raises(ValueError, match="姓名重复"):
        admin_seeds.check_unique_names(
            {"sys_user": [{"code": "A", "name": "甲"}, {"code": "B", "name": "甲"}]}
        )

    # 端到端：SYS_USERS 里把赵蕾改名成王敏 → run_seeds 抛错，一行都不写
    def dup(r, ctx):
        row = admin_seeds._user(r, ctx)
        return {**row, "name": "王敏"} if row["code"] == "U010007" else row

    specs = [
        dataclasses.replace(s, transform=dup) if s.file == "SYS_USERS.json" else s for s in admin_seeds.SEEDS
    ]
    monkeypatch.setattr(admin_seeds, "SEEDS", specs)
    await session.execute(update(SysUser).where(SysUser.code == "U010001").values(dept="x"))
    with pytest.raises(ValueError, match="王敏"):
        await run_seeds(session, ["admin"])
    # 跨文件也查：SYS_USERS 里的人和 ADMIN_USERS 的苏刚重名 → 报错
    specs2 = [
        dataclasses.replace(s, transform=lambda r, c: {**admin_seeds._user(r, c), "name": "苏刚"})
        if s.file == "SYS_USERS.json"
        else s
        for s in admin_seeds.SEEDS
    ]
    monkeypatch.setattr(admin_seeds, "SEEDS", specs2)
    with pytest.raises(ValueError, match="苏刚"):
        await run_seeds(session, ["admin"])
    assert await session.scalar(select(SysUser.name).where(SysUser.code == "U010007")) == "赵蕾"
    assert await session.scalar(select(SysUser.dept).where(SysUser.code == "U010001")) == "x"  # 没被覆盖


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


async def test_apply_row_filter_injection(monkeypatch, users):
    from sqlalchemy.dialects import postgresql

    import app.common.auth as auth
    from app.common.row_filter import apply_row_filter

    user = await auth.get_current_user(users)  # 王敏，本体管理员
    stmt = select(Widget)
    assert await apply_row_filter(stmt, user, "widget", {}) is stmt  # 桩：管理员不限制
    viewer = await auth.get_current_user(users, x_demo_as="U010006")  # 李娜
    assert await auth.row_filter(viewer, "widget") is None  # 初版最简：已登录一律不裁剪
    assert await apply_row_filter(stmt, viewer, "widget", {}) is stmt

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


def test_seed_order():
    """加载顺序（CONVENTIONS §9「加载顺序」）。改顺序要同时改文档。"""
    from app.seeds.loader import module_group, module_specs
    from app.seeds.registry import SEED_MODULES, SEED_STAGES, stage_module, stage_package

    assert SEED_MODULES == [
        "admin",
        "data",
        "integration",
        "biz",
        "versioning",
        "static",
        "action",
        "event",
        "dynamic",
        "integration_runs",
        "qa",
    ]
    order = SEED_MODULES.index
    assert order("versioning") < order("static")  # 静态外键 release_id 要先有版本行
    assert order("integration") < order("versioning")  # ext_system 被引用
    assert order("dynamic") < order("integration_runs") < order("qa")
    assert set(SEED_STAGES) == {"integration_runs"}
    assert stage_module("integration_runs") == "integration"
    assert stage_package("integration_runs") == "app.modules.integration.seeds.runs"
    assert stage_package("static") == "app.modules.static.seeds"
    assert module_group("integration_runs") == module_group("integration") == "system"
    for stage in SEED_MODULES:  # 每个阶段的 seeds 包都能导入
        module_specs(stage)
