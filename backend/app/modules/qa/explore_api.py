"""通用对象搜索 / 详情（「使用」区通用化，设计见 backend/docs/backend/generic-object-explorer.md）：
按对象类型 + 筛选 + 关键词 + 排序 + 分页取行，行按属性 api 名给值。
挂在 qa router 上（router.py 末尾 include）。

- 元数据只读静态的 object_type / property（不写）。属性标志（searchable / filterable / sortable）读静态
  property 列（static_0004），见 `flags`；真实接口失败不回退演示数据。
- 分面归静态 `GET /object-types/{id}/facets`。静态上线前，搜索结果里顺带给一份同形状的
  `facets`（过渡，[推断]），前端优先读静态的；静态上线后删掉这里的 `_facets`。
- 物理绑定：seeds/extra/qa/objects/{本体}/bindings.json（按本体分目录；对象类型 → 表 + 属性 → 列）。演示数据在 biz 表，不是
  object_type.source_table [推断]。没有绑定的对象类型返回 bound=false、空结果，前端显示「未接入数据」。
- 没有绑定文件的本体一律 bound=false；其他来源须按 object-read-adapter.md 显式登记工作空间/对象/源表白名单与专用只读连接。
- 行级范围：绑定里写了 rowFilter 的，走骨架 apply_row_filter + biz/governance.py 的登记列；没写的按 row_filter
  是否限制决定：限制（含 DENY_ALL）时一律不返回（宁严勿宽）[推断]。
"""

import csv
import io
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from pydantic import ConfigDict, Field
from sqlalchemy import Select, String, asc, cast, desc, false, func, or_, select

from app.common.audit import audit
from app.common.auth import CurrentUser, CurrentUserDep, check_func, require_func, row_filter
from app.common.context import ViewContextDep, readonly_extra
from app.common.ontology import require_ontology_id
from app.common.pagination import MAX_PAGE_SIZE
from app.common.row_filter import apply_row_filter
from app.core.config import get_settings
from app.core.errors import AppError, ExportForbidden, NotFound
from app.core.schema import DATETIME_SEC_FORMAT, ApiModel, _tz
from app.db.base import Base
from app.db.session import SessionDep
from app.modules.admin.exports import csv_cell
from app.modules.biz.governance import ORDER_COLUMNS, ORDER_LINKS, PRODUCT_COLUMNS, STORE_COLUMNS
from app.modules.qa import service as svc
from app.modules.qa.object_source import (
    can_export_object,
    check_object_read,
    configured_bindings,
    object_definition,
    primary_property,
    require_object_read,
    resolve_source,
)
from app.modules.static.explorer import bad_filter, facet_kind, facetable, scalar_filter
from app.modules.static.models import ObjectType, Property


class ValidationFailed(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "参数不合法"


explore = APIRouter(dependencies=[Depends(require_object_read)])

# 行级范围登记（biz/governance.py）：绑定里的 rowFilter → (列, 跨对象路径)
_GOV: dict[str, tuple[dict, dict | None]] = {
    "store": (STORE_COLUMNS, None),
    "product": (PRODUCT_COLUMNS, None),
    "order": (ORDER_COLUMNS, ORDER_LINKS),
}
NUMERIC = {"decimal", "integer", "int"}
TIME = {"date", "timestamp"}
FACET_VALUES = 50  # 单个枚举分面最多给多少个取值（按计数降序）


@lru_cache
def bindings(ontology_id: int) -> dict[str, dict]:
    """本体的对象物理绑定：seeds/extra/qa/objects/{本体}/bindings.json；没有文件 = 未接入（空）。"""
    path = get_settings().seeds_extra_dir / "qa" / "objects" / str(ontology_id) / "bindings.json"
    if type(ontology_id) is not int or ontology_id <= 0 or not path.exists():
        return {}
    return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def visible(p: Property) -> bool:
    """能对外给值：不敏感、不隐藏（搜索结果 / 详情 / 导出 / 排序都按这个裁）。"""
    return not p.is_sensitive and p.visibility != "hidden"


def flags(p: Property) -> dict[str, bool]:
    """属性标志读静态 property 上的 searchable / filterable / sortable（static_0004）。

    可筛选口径与静态 meta / facets 一致：facetable 已排除敏感、隐藏属性。"""
    return {
        "searchable": bool(p.searchable) and visible(p),
        "filterable": facetable(p),
        "sortable": bool(p.sortable) and visible(p),
    }


class ExploreIn(ApiModel):
    model_config = ConfigDict(extra="forbid")
    kw: str = Field("", max_length=100, pattern=r"^[^\x00]*$")
    # 属性 api 名 → 取值列表（enum / 字符串 / 布尔）或 {min,max}（数值 / 时间，闭区间；只给日期时含当天）
    # 值的形状按属性逐个校验（Bound.cond，同静态 /facets）：不合法一律 422，不让数据库报 500
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str | None = Field(None, description="属性 api 名，前缀 - 表示倒序")
    page: int = Field(1, ge=1)
    size: int = Field(10, ge=1, le=MAX_PAGE_SIZE)
    with_facets: bool = Field(True, description="顺带返回过渡分面（静态 /facets 上线后去掉）")


def _out(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=_tz())).astimezone(_tz()).strftime(DATETIME_SEC_FORMAT)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _bound(v: Any, dt: str, upper: bool, key: str = "") -> tuple[Any, bool]:
    """区间端点 → (值, 是否开区间)。空 = 不限；timestamp 只给日期时，下界取当天 0 点，
    上界取次日 0 点（开）。"""
    if v is None or v == "":
        return None, False
    s = str(v)
    if "\x00" in s:
        bad_filter(key, "不能包含 NUL 字符")
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        bad_filter(key, "含无效 Unicode 字符")
    if dt in TIME and (not isinstance(v, str) or len(s) > 64):
        bad_filter(key, "日期应为 ISO 字符串，如 2026-10-01")
    try:
        if dt == "date":
            return date.fromisoformat(s), False
        if dt == "timestamp":
            if len(s) <= 10:
                d = datetime.combine(date.fromisoformat(s), datetime.min.time(), _tz())
                return (d + timedelta(days=1), True) if upper else (d, False)
            d = datetime.fromisoformat(s)
            return (d if d.tzinfo else d.replace(tzinfo=_tz())), False
        if dt in NUMERIC:
            return Decimal(s), False
    except (ValueError, InvalidOperation, OverflowError):
        bad_filter(key, "日期 / 数值格式不对")
    return v, False


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _type(session: Any, code: str) -> ObjectType:
    t = (await session.execute(select(ObjectType).where(ObjectType.code == code))).scalar_one_or_none()
    if t is None:
        raise NotFound("对象类型不存在", details={"objectType": code})
    return t


class Bound:
    """一个对象类型的绑定：FROM 子句、属性 → 列、行 id 列。表 / 列名都来自绑定文件，不来自请求。"""

    def __init__(self, t: ObjectType, b: dict, source=None) -> None:
        self.source = source
        tables = {b["table"]: source.table} if source is not None else Base.metadata.tables
        base = tables[b["table"]]
        alias: dict[str, Any] = {"": base}
        frm: Any = base
        for j in b.get("joins") or []:
            a = tables[j["table"]].alias(j["alias"])
            alias[j["alias"]] = a
            frm = frm.outerjoin(a, base.c[j["on"][0]] == a.c[j["on"][1]])

        def col(ref: str) -> Any:
            a, _, c = ref.rpartition(".")
            return alias[a].c[c]

        self.props = {p.api_name: p for p in t.properties}
        primary = primary_property(t)
        pk = primary.api_name if primary is not None else None
        self.title = next((p.api_name for p in t.properties if p.is_title), pk)
        self.cols = {k: col(v) for k, v in b["columns"].items() if k in self.props}  # 只认静态里有的属性
        # 对外给值的属性：敏感 / 隐藏的只在后台用（行级范围、id），不出现在结果、详情、导出里
        self.out = [k for k in self.cols if visible(self.props[k])]
        self.id_col = col(b["id"])
        if pk not in self.cols or not visible(self.props[pk]) or not self.cols[pk].compare(self.id_col):
            raise AppError("对象主键未配置公开的可读取映射，当前无法读取实例", code="OBJECT_IDENTIFIER_UNAVAILABLE", status_code=503)
        self.frm, self.base, self.b, self.code = frm, base, b, t.code

    @asynccontextmanager
    async def read(self, session):
        if self.source is None:
            yield session
        else:
            async with self.source.read() as conn:
                yield conn

    def id_value(self, value):
        return scalar_filter(self.id_col, value, "id")

    async def stmt(self, viewer: CurrentUser) -> Select:
        check_object_read(viewer)
        stmt: Select = select(self.id_col.label("__id"), *[c.label(k) for k, c in self.cols.items()])
        stmt = stmt.select_from(self.frm)
        for k, v in (self.b.get("where") or {}).items():
            stmt = stmt.where(self.base.c[k] == v)
        if self.source is not None:
            # Unsupported cross-object predicates are rejected by the shared compiler.
            return await apply_row_filter(stmt, viewer, self.code, self.cols)
        gov = self.b.get("rowFilter")
        if gov in _GOV:
            return await apply_row_filter(stmt, viewer, gov, *_GOV[gov])
        if await row_filter(viewer, self.code) is not None:
            return stmt.where(false())
        return stmt

    def check(self, keys: Any, what: str) -> None:
        """筛选键须可筛选（facetable：filterable 且不敏感、不隐藏）；排序键须 sortable 且不敏感、不隐藏。"""
        flag = "filterable" if what == "筛选" else "sortable"
        field = "filters" if what == "筛选" else "sort"
        for k in keys:
            if k not in self.cols or not flags(self.props[k])[flag]:
                f_ = f"filters.{k}" if field == "filters" else "sort"
                msg = f"属性 {k} 不存在、未接入数据或不可{what}"
                raise ValidationFailed(
                    f"{what}属性不合法", details={"fields": [{"field": f_, "message": msg}]}
                )

    def cond(self, k: str, v: Any) -> list[Any]:
        """一个筛选 → SQL 条件，校验同静态 explorer._cond：枚举类只收标量数组（未知枚举值 422），
        区间类只收 {min, max}；timestamp 只给日期时上界含当天。"""
        p, c = self.props[k], self.cols[k]
        if facet_kind(p) == "enum":
            if not isinstance(v, list) or any(isinstance(x, list | dict) or x is None for x in v):
                bad_filter(k, '枚举类筛选应为取值数组，如 ["营业中"]')
            return [c.in_([scalar_filter(c, x, k) for x in v])] if v else []
        if not isinstance(v, dict) or not v or set(v) - {"min", "max"}:
            bad_filter(k, "区间类筛选应为 {min, max}")
        for x in v.values():
            if x is not None and (isinstance(x, bool) or not isinstance(x, str | int | float)):
                bad_filter(k, "min / max 应为数字或日期字符串")
        out: list[Any] = []
        for end in ("min", "max"):
            x = v.get(end)
            if x is None or x == "":
                continue
            if p.data_type in TIME:
                val, open_hi = _bound(x, p.data_type, end == "max", k)
            else:
                val, open_hi = scalar_filter(c, x, k), False
            out.append(c >= val if end == "min" else (c < val if open_hi else c <= val))
        return out

    def where(self, stmt: Select, body: ExploreIn, skip: str | None = None) -> Select:
        kw = body.kw.strip()
        if kw:
            # 没有可搜索属性就忽略关键词（不拿别的列兜底匹配，免得按敏感 / 隐藏列命中）
            keys = [k for k in self.out if flags(self.props[k])["searchable"]]
            pat = f"%{_esc(kw)}%"
        if kw and keys:
            stmt = stmt.where(or_(*[cast(self.cols[k], String).ilike(pat, escape="\\") for k in keys]))
        for k, v in body.filters.items():
            if k == skip:
                continue
            for w in self.cond(k, v):
                stmt = stmt.where(w)
        return stmt


async def _bind(session: Any, code: str, ver: str | None = None) -> tuple[ObjectType, Bound | None]:
    t = await object_definition(session, code, ver)
    source = await resolve_source(session, t)
    if source is not None:
        return t, Bound(t, {"table": source.table.name, "id": source.id_column, "columns": source.columns}, source)
    b = bindings(require_ontology_id(session)).get(code)
    # An explicitly configured but incomplete source must never silently borrow the retail binding.
    if any(row["ontologyId"] == require_ontology_id(session) and row["objectType"] == code
           for row in configured_bindings()):
        return t, None
    return t, Bound(t, b) if b else None


async def _facets(session: Any, bd: Bound, base: Select, body: ExploreIn) -> list[dict]:
    """过渡分面 [推断]：可筛选属性；枚举类给取值计数，数值 / 时间给 min/max。
    每个分面去掉自己的筛选（联动）。"""
    out: list[dict] = []
    for k in bd.cols:
        p = bd.props[k]
        if not flags(p)["filterable"]:
            continue
        sub = bd.where(base, body, skip=k).subquery()
        sc = sub.c[k]
        if p.data_type in NUMERIC | TIME:
            lo, hi = (await session.execute(select(func.min(sc), func.max(sc)))).one()
            if lo is not None:
                out.append({"key": k, "kind": "range", "min": _out(lo), "max": _out(hi)})
            continue
        rows = await session.execute(
            select(sc, func.count())
            .where(sc.is_not(None))
            .group_by(sc)
            .order_by(func.count().desc(), sc)
            .limit(FACET_VALUES)
        )
        from app.modules.static.explorer import ExplorerService

        out.append({"key": k, "kind": "enum", "values": ExplorerService._enum_values(sc, list(rows))})
    return out


def _item(bd: Bound, r: Any) -> dict:
    title = r[bd.title] if bd.title in bd.out else None
    return {
        "id": str(r["__id"]),
        "title": str(_out(title)) if title is not None else str(r["__id"]),
        "values": {k: _out(r[k]) for k in bd.out},
    }


@explore.get("/object-types", summary="使用区已发布对象类型目录")
async def explore_types(session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep,
                        page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=MAX_PAGE_SIZE)):
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    check_object_read(viewer)
    if ctx.ver:
        from app.modules.versioning.repo import ReleaseRepo

        release = await ReleaseRepo(session).by_ver_or_404(ctx.ver)
        rows = [obj for obj in ((release.snapshot or {}).get("objectTypes") or {}).values() if obj.get("status") == "已发布"]
        total = len(rows)
        actual = min(page, max(1, -(-total // size)))
        items = [{"id": obj["id"], "name": obj["name"], "group": obj["group"], "status": "已发布"} for obj in rows[(actual - 1) * size:actual * size]]
    else:
        from app.modules.static.models import ObjectStatus

        stmt = select(ObjectType.code, ObjectType.display_name, ObjectType.obj_group).where(ObjectType.status == ObjectStatus.published)
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        actual = min(page, max(1, -(-total // size)))
        items = [{"id": code, "name": name, "group": group, "status": "已发布"} for code, name, group in
                 await session.execute(stmt.order_by(ObjectType.seq, ObjectType.code).offset((actual - 1) * size).limit(size))]
    return {"items": items, "total": total, "page": actual, "size": size}


@explore.get("/object-types/{code}/meta", summary="使用区对象属性与能力元数据")
async def explore_meta(code: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    from app.modules.static.explorer import ExplorerService

    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    check_object_read(viewer)
    return await ExplorerService(session).meta(code, ctx.ver)


@explore.post(
    "/object-types/{code}/search",
    dependencies=[Depends(require_func("object-search", "view"))],
    summary="通用对象搜索：按属性 api 名返回行（元数据驱动，物理绑定见 seeds/extra/qa/objects）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def explore_search(
    code: str, body: ExploreIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    t, bd = await _bind(session, code, ctx.ver)
    title = next((p.api_name for p in t.properties if p.is_title), t.pk_property)
    empty = {
        "items": [],
        "total": 0,
        "page": 1,
        "size": body.size,
        "titleProp": title,
        "bound": False,
        "boundProps": [],
        "facets": [],
        "canExport": False,
        "dataVersion": "current",
    }
    if bd is None:
        return empty
    bd.check(body.filters, "筛选")
    if body.sort:
        bd.check([body.sort.removeprefix("-")], "排序")
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    check_func(viewer, "object-search", "view")
    empty["canExport"] = await can_export_object(session, viewer, code) and not ctx.preview_as
    base = await bd.stmt(viewer)
    stmt = bd.where(base, body)
    if body.sort:
        c = bd.cols[body.sort.removeprefix("-")]
        stmt = stmt.order_by((desc if body.sort.startswith("-") else asc)(c).nulls_last(), bd.id_col)
    else:
        stmt = stmt.order_by(bd.id_col)
    async with bd.read(session) as reader:
        total = (await reader.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        page = min(body.page, max(1, -(-total // body.size)))
        rows = (await reader.execute(stmt.offset((page - 1) * body.size).limit(body.size))).mappings().all()
        facets = await _facets(reader, bd, base, body) if body.with_facets else []
    return {
        **empty,
        "items": [_item(bd, r) for r in rows],
        "total": total,
        "page": page,
        "bound": True,
        "boundProps": bd.out,
        "facets": facets,
    }


@explore.get(
    "/object-types/{code}/objects/{id}",
    summary="通用对象详情：单个对象的属性值（同一套绑定和行级范围）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def explore_get(code: str, id: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, bd = await _bind(session, code, ctx.ver)
    if bd is None:
        raise NotFound("该对象类型未接入数据", details={"objectType": code, "id": id})
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    stmt = (await bd.stmt(viewer)).where(bd.id_col == bd.id_value(id))
    async with bd.read(session) as reader:
        r = (await reader.execute(stmt.limit(1))).mappings().first()
    if r is None:
        # Do not query outside the authorized predicate merely to distinguish existence.
        raise NotFound("对象不存在", details={"objectType": code, "id": id})
    return {**_item(bd, r), "titleProp": bd.title, "bound": True, "boundProps": bd.out, "dataVersion": "current"}


class ExportIn(ExploreIn):
    ids: list[str] | None = Field(None, max_length=5000, description="只导出选中的对象（仍按范围和筛选裁剪）")
    props: list[str] | None = Field(
        None, description="导出哪些属性（默认全部已接入属性）；敏感属性一律不导出"
    )


EXPORT_MAX = 10000


@explore.post(
    "/object-types/{code}/export",
    dependencies=[Depends(require_func("object-search", "view"))],
    summary="通用对象导出（CSV，UTF-8 BOM；首列标题属性，敏感属性不导出）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def explore_export(
    code: str, body: ExportIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    t, bd = await _bind(session, code, ctx.ver)
    if ctx.preview_as:
        raise ExportForbidden(details={"reason": "预览中不能导出"})
    if bd is None:
        raise NotFound("该对象类型未接入数据", details={"objectType": code})
    bd.check(body.filters, "筛选")
    keys = [k for k in (body.props or bd.out) if k in bd.out]  # 敏感、隐藏属性一律不导出
    keys = [k for k in keys if k != bd.title]
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    check_func(viewer, "object-search", "view")
    if not await can_export_object(session, viewer, code):
        raise ExportForbidden("没有此对象类型的导出权限")
    stmt = bd.where(await bd.stmt(viewer), body)
    if body.ids is not None:
        stmt = stmt.where(bd.id_col.in_([bd.id_value(value) for value in body.ids]))
    async with bd.read(session) as reader:
        rows = (await reader.execute(stmt.order_by(bd.id_col).limit(EXPORT_MAX + 1))).mappings().all()
    if len(rows) > EXPORT_MAX:
        raise ValidationFailed(f"单次最多导出 {EXPORT_MAX} 条，请缩小筛选范围")
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    headers = [bd.props[bd.title].display_name if bd.title in bd.out else "ID", *[bd.props[k].display_name for k in keys]]
    w.writerow([csv_cell(str(value)) for value in headers])
    for r in rows:
        it = _item(bd, r)
        w.writerow([csv_cell(str(value)) for value in [it["title"], *["" if it["values"][k] is None else it["values"][k] for k in keys]]])
    name = f"{t.display_name}_{len(rows)}.csv"
    detail = {"objectType": code, "rows": len(rows), "selected": bool(body.ids)}
    await audit(session, user, action="qa.objects.export", target=code, detail=detail)
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


async def explore_ids(
    session: Any, viewer: CurrentUser, code: str, body: ExploreIn, limit: int = 5000
) -> list[str]:
    """按同一套筛选取范围内的对象 id（O4「用这批对象提问」用），最多 limit 个。"""
    _, bd = await _bind(session, code)
    if bd is None:
        return []
    bd.check(body.filters, "筛选")
    stmt = bd.where(await bd.stmt(viewer), body).order_by(bd.id_col).limit(limit + 1)
    async with bd.read(session) as reader:
        ids = [str(r["__id"]) for r in (await reader.execute(stmt)).mappings().all()]
    if len(ids) > limit:
        raise ValidationFailed("此问题的对象范围过大，请缩小筛选范围或使用对象数量汇总")
    return ids


@explore.get("/object-types/{code}/objects/{id}/related", summary="按已发布关系和两端权限读取关联对象")
async def explore_related(code: str, id: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep,
                          link: str | None = None, page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=MAX_PAGE_SIZE)):
    from app.modules.static.models import DefStatus, LinkType
    from app.modules.static.shapes import link as link_shape
    from app.modules.versioning.repo import ReleaseRepo

    _, source = await _bind(session, code, ctx.ver)
    if source is None:
        raise NotFound("该对象类型未接入数据")
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    origin_stmt = (await source.stmt(viewer)).where(source.id_col == source.id_value(id))
    async with source.read(session) as reader:
        origin = (await reader.execute(origin_stmt.limit(1))).mappings().first()
    if origin is None:
        raise NotFound("对象不存在或不在可见范围内")
    if ctx.ver:
        release = await ReleaseRepo(session).by_ver_or_404(ctx.ver)
        links = list(((release.snapshot or {}).get("links") or {}).values())
    else:
        links = [link_shape(row, with_runtime=False) for row in await session.scalars(
            select(LinkType).where(LinkType.status == DefStatus.published).order_by(LinkType.seq, LinkType.id))]
    out = []
    for definition in links:
        if definition.get("status") != "已发布" or code not in (definition.get("from"), definition.get("to")):
            continue
        if link and definition["id"] != link:
            continue
        forward = definition["from"] == code
        other = definition["to"] if forward else definition["from"]
        local_key = definition["fromProp"] if forward else definition["toProp"]
        remote_key = definition["toProp"] if forward else definition["fromProp"]
        group = {"linkId": definition["id"], "label": definition["name"] if forward else definition.get("reverse") or definition["name"],
                 "other": other, "items": [], "total": 0, "page": 1, "size": size, "state": "ok", "dataVersion": "current"}
        if definition.get("via") or local_key not in source.out:
            out.append({**group, "state": "unsupported"})
            continue
        try:
            _, target = await _bind(session, other, ctx.ver)
        except NotFound:
            target = None
        if target is None:
            out.append({**group, "state": "unbound"})
            continue
        same_source = (source.source is None and target.source is None) or (
            source.source is not None and target.source is not None and source.source.datasource == target.source.datasource
            and source.source.url == target.source.url)
        if not same_source or remote_key not in target.out:
            out.append({**group, "state": "unsupported"})
            continue
        value = origin[local_key]
        if value is None:
            out.append(group)
            continue
        stmt = (await target.stmt(viewer)).where(target.cols[remote_key] == scalar_filter(target.cols[remote_key], value, remote_key))
        async with target.read(session) as reader:
            total = (await reader.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
            actual_page = min(page, max(1, -(-total // size)))
            rows = (await reader.execute(stmt.order_by(target.id_col).offset((actual_page - 1) * size).limit(size))).mappings().all()
        out.append({**group, "items": [_item(target, row) for row in rows], "total": total, "page": actual_page})
    if link and not out:
        raise NotFound("该版本中对象关系不存在")
    return out


def scope_operation(question, properties):
    """Only explicit count/property aggregate labels; this is not a natural-language SQL parser."""
    if question in ("对象数量", "数量", "有多少个对象", "共多少个对象"):
        return "count", None
    operations = {"合计": "sum", "平均值": "avg", "最小值": "min", "最大值": "max"}
    matches = [(operation, prop.api_name) for suffix, operation in operations.items()
               for prop in properties if visible(prop) and prop.data_type in NUMERIC
               and question in (f"{prop.display_name}{suffix}", f"{prop.api_name}{suffix}")]
    return matches[0] if len(matches) == 1 else None


def scoped_questions(binding, properties):
    if binding is None:
        return []
    questions = ["对象数量"]
    numeric = sorted((p for p in properties if p.api_name in binding.out and p.data_type in NUMERIC),
                     key=lambda p: (not bool(p.list_visible), p.seq, p.api_name))
    for prop in numeric:
        for suffix, operation in (("合计", "sum"), ("平均值", "avg")):
            question = f"{prop.display_name}{suffix}"
            if scope_operation(question, properties) != (operation, prop.api_name):
                question = f"{prop.api_name}{suffix}"
            if scope_operation(question, properties) == (operation, prop.api_name):
                questions.append(question)
        if len(questions) > 1:
            break
    return questions[:3]


async def scoped_summary(session, viewer, ctx, scope, question):
    code = scope.single.kind if scope.single else scope.kind
    if scope.single and scope.single.kind != scope.kind:
        raise ValidationFailed("单个对象与提问范围的对象类型不一致")
    obj, binding = await _bind(session, code, ctx.ver)
    operation = scope_operation(question, obj.properties)
    if operation is None:
        return None
    if binding is None:
        raise NotFound("该对象类型未接入数据")
    op, prop = operation
    if prop is not None and prop not in binding.out:
        raise ValidationFailed("该属性未配置可读取的数值映射")
    body = ExploreIn(**(scope.query or {}))
    binding.check(body.filters, "筛选")
    stmt = binding.where(await binding.stmt(viewer), body)
    if scope.single:
        stmt = stmt.where(binding.id_col == binding.id_value(scope.single.id))
    elif scope.ids is not None:
        stmt = stmt.where(binding.id_col.in_([binding.id_value(value) for value in scope.ids]))
    elif scope.filter is not None and not scope.query:
        raise ValidationFailed("通用对象范围请使用属性筛选条件")
    rows = stmt.subquery()
    value = func.count() if op == "count" else {"sum": func.sum, "avg": func.avg, "min": func.min, "max": func.max}[op](rows.c[prop])
    async with binding.read(session) as reader:
        count, result = (await reader.execute(select(func.count(), value).select_from(rows))).one()
    label = "对象数量" if op == "count" else question
    text = f"当前可见的{obj.display_name}共 {count} 个。" if op == "count" else f"当前可见的{obj.display_name}共 {count} 个，{label}：{_out(result) if result is not None else '暂无数据'}。"
    return {"text": text,
            "basis": "当前授权实例范围的直接汇总" + (f" · 定义版本 {ctx.ver}" if ctx.ver else "") + " · 未套用指标口径",
            "rows": []}
