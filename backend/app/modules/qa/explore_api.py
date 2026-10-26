"""通用对象搜索 / 详情（「使用」区通用化，设计见 backend/docs/backend/generic-object-explorer.md）：
按对象类型 + 筛选 + 关键词 + 排序 + 分页取行，行按属性 api 名给值。
挂在 qa router 上（router.py 末尾 include）。

- 元数据只读静态的 object_type / property（不写）。属性标志（searchable / filterable / sortable）读静态
  property 列（static_0004），见 `flags`；前端 objMeta 只在静态后台没起来时用 mock + 默认规则 [推断]。
- 分面归静态 `GET /object-types/{id}/facets`。静态上线前，搜索结果里顺带给一份同形状的
  `facets`（过渡，[推断]），前端优先读静态的；静态上线后删掉这里的 `_facets`。
- 物理绑定：seeds/extra/qa/objects/bindings.json（对象类型 → 表 + 属性 → 列）。演示数据在 biz 表，不是
  object_type.source_table [推断]。没有绑定的对象类型返回 bound=false、空结果，前端显示「未接入数据」。
- 行级范围：绑定里写了 rowFilter 的，走骨架 apply_row_filter + biz/governance.py 的登记列；没写的按 row_filter
  是否限制决定：限制（含 DENY_ALL）时一律不返回（宁严勿宽）[推断]。
"""

import csv
import io
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Response
from pydantic import Field
from sqlalchemy import Select, String, asc, cast, desc, false, func, or_, select

from app.common.audit import audit
from app.common.auth import CurrentUser, CurrentUserDep, require_func, row_filter
from app.common.context import ViewContextDep, readonly_extra
from app.common.pagination import MAX_PAGE_SIZE
from app.common.row_filter import apply_row_filter
from app.core.config import get_settings
from app.core.errors import AppError, ExportForbidden, NotFound
from app.core.schema import DATETIME_SEC_FORMAT, ApiModel, _tz
from app.db.base import Base
from app.db.session import SessionDep
from app.modules.biz.governance import ORDER_COLUMNS, ORDER_LINKS, PRODUCT_COLUMNS, STORE_COLUMNS
from app.modules.qa import service as svc
from app.modules.qa.objects_api import OutOfScope
from app.modules.static.explorer import facetable
from app.modules.static.models import ObjectType, Property


class ValidationFailed(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "参数不合法"


explore = APIRouter(dependencies=[Depends(require_func("object-search", "view"))])

# 行级范围登记（biz/governance.py）：绑定里的 rowFilter → (列, 跨对象路径)
_GOV: dict[str, tuple[dict, dict | None]] = {
    "store": (STORE_COLUMNS, None),
    "product": (PRODUCT_COLUMNS, None),
    "order": (ORDER_COLUMNS, ORDER_LINKS),
}
NUMERIC = {"decimal", "integer"}
TIME = {"date", "timestamp"}
FACET_VALUES = 50  # 单个枚举分面最多给多少个取值（按计数降序）


@lru_cache
def bindings() -> dict[str, dict]:
    path = get_settings().seeds_extra_dir / "qa" / "objects" / "bindings.json"
    return {k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if not k.startswith("_")}


def flags(p: Property) -> dict[str, bool]:
    """属性标志读静态 property 上的 searchable / filterable / sortable（static_0004）。

    可筛选口径与静态 meta / facets 一致：facetable 已排除敏感、隐藏属性。"""
    return {"searchable": bool(p.searchable), "filterable": facetable(p), "sortable": bool(p.sortable)}


class RangeIn(ApiModel):
    min: Any = None
    max: Any = None


class ExploreIn(ApiModel):
    kw: str = Field("", max_length=100)
    # 属性 api 名 → 取值列表（enum / 字符串 / 布尔）或 {min,max}（数值 / 时间，闭区间；只给日期时含当天）
    filters: dict[str, list[Any] | RangeIn] = Field(default_factory=dict)
    sort: str | None = Field(None, description="属性 api 名，前缀 - 表示倒序")
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=MAX_PAGE_SIZE)
    with_facets: bool = Field(True, description="顺带返回过渡分面（静态 /facets 上线后去掉）")


def _out(v: Any) -> Any:
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.astimezone(_tz()).strftime(DATETIME_SEC_FORMAT)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _bound(v: Any, dt: str, upper: bool) -> tuple[Any, bool]:
    """区间端点 → (值, 是否开区间)。空 = 不限；timestamp 只给日期时，下界取当天 0 点，
    上界取次日 0 点（开）。"""
    if v is None or v == "":
        return None, False
    s = str(v)
    try:
        if dt == "date":
            return date.fromisoformat(s[:10]), False
        if dt == "timestamp":
            if len(s) <= 10:
                d = datetime.combine(date.fromisoformat(s), datetime.min.time(), _tz())
                return (d + timedelta(days=1), True) if upper else (d, False)
            d = datetime.fromisoformat(s)
            return (d if d.tzinfo else d.replace(tzinfo=_tz())), False
        if dt in NUMERIC:
            return Decimal(s), False
    except (ValueError, InvalidOperation) as e:
        raise ValidationFailed("筛选区间格式不对", details={"value": v, "type": dt}) from e
    return v, False


def _member(x: Any, c: Any) -> Any:
    """请求给的是显示值（营业中 / true）：enum 列换成枚举成员（库里存成员名），布尔列换成 bool。"""
    enum_cls = getattr(c.type, "enum_class", None)
    if enum_cls is not None:
        return next((m for m in enum_cls if m.value == x or m.name == x), None)
    try:
        py = c.type.python_type
    except NotImplementedError:
        py = None
    if py is bool:
        return x if isinstance(x, bool) else str(x).lower() in {"true", "1", "是"}
    return x


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _type(session: Any, code: str) -> ObjectType:
    t = (await session.execute(select(ObjectType).where(ObjectType.code == code))).scalar_one_or_none()
    if t is None:
        raise NotFound("对象类型不存在", details={"objectType": code})
    return t


class Bound:
    """一个对象类型的绑定：FROM 子句、属性 → 列、行 id 列。表 / 列名都来自绑定文件，不来自请求。"""

    def __init__(self, t: ObjectType, b: dict) -> None:
        tables = Base.metadata.tables
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
        self.title = next((p.api_name for p in t.properties if p.is_title), t.pk_property)
        self.cols = {k: col(v) for k, v in b["columns"].items() if k in self.props}  # 只认静态里有的属性
        self.id_col = col(b["id"])
        self.frm, self.base, self.b, self.code = frm, base, b, t.code

    async def stmt(self, viewer: CurrentUser) -> Select:
        stmt: Select = select(self.id_col.label("__id"), *[c.label(k) for k, c in self.cols.items()])
        stmt = stmt.select_from(self.frm)
        for k, v in (self.b.get("where") or {}).items():
            stmt = stmt.where(self.base.c[k] == v)
        gov = self.b.get("rowFilter")
        if gov in _GOV:
            return await apply_row_filter(stmt, viewer, gov, *_GOV[gov])
        if await row_filter(viewer, self.code) is not None:
            return stmt.where(false())
        return stmt

    def check(self, keys: Any, what: str) -> None:
        for k in keys:
            if k not in self.cols:
                raise ValidationFailed(f"{what}属性不存在或未接入数据", details={"property": k})

    def where(self, stmt: Select, body: ExploreIn, skip: str | None = None) -> Select:
        kw = body.kw.strip()
        if kw:
            keys = [k for k in self.cols if flags(self.props[k])["searchable"]] or list(self.cols)[:1]
            pat = f"%{_esc(kw)}%"
            stmt = stmt.where(or_(*[cast(self.cols[k], String).ilike(pat, escape="\\") for k in keys]))
        for k, v in body.filters.items():
            if k == skip:
                continue
            c, dt = self.cols[k], self.props[k].data_type
            if isinstance(v, RangeIn):
                lo, _ = _bound(v.min, dt, False)
                hi, open_hi = _bound(v.max, dt, True)
                if lo is not None:
                    stmt = stmt.where(c >= lo)
                if hi is not None:
                    stmt = stmt.where(c < hi if open_hi else c <= hi)
            elif v:
                stmt = stmt.where(c.in_([_member(x, c) for x in v]))
        return stmt


async def _bind(session: Any, code: str) -> tuple[ObjectType, Bound | None]:
    t = await _type(session, code)
    b = bindings().get(code)
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
        out.append({"key": k, "kind": "enum", "values": [{"value": _out(v), "count": n} for v, n in rows]})
    return out


def _item(bd: Bound, r: Any) -> dict:
    title = r[bd.title] if bd.title in bd.cols else None
    return {
        "id": r["__id"],
        "title": _out(title) if title is not None else r["__id"],
        "values": {k: _out(r[k]) for k in bd.cols},
    }


@explore.post(
    "/object-types/{code}/search",
    summary="通用对象搜索：按属性 api 名返回行（元数据驱动，物理绑定见 seeds/extra/qa/objects）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def explore_search(
    code: str, body: ExploreIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    t, bd = await _bind(session, code)
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
    }
    if bd is None:
        return empty
    bd.check(body.filters, "筛选")
    if body.sort:
        bd.check([body.sort.lstrip("-")], "排序")
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    base = await bd.stmt(viewer)
    stmt = bd.where(base, body)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    if body.sort:
        c = bd.cols[body.sort.lstrip("-")]
        stmt = stmt.order_by((desc if body.sort.startswith("-") else asc)(c).nulls_last(), bd.id_col)
    else:
        stmt = stmt.order_by(bd.id_col)
    page = min(body.page, max(1, -(-total // body.size)))
    rows = (await session.execute(stmt.offset((page - 1) * body.size).limit(body.size))).mappings().all()
    return {
        **empty,
        "items": [_item(bd, r) for r in rows],
        "total": total,
        "page": page,
        "bound": True,
        "boundProps": list(bd.cols),
        "facets": await _facets(session, bd, base, body) if body.with_facets else [],
    }


@explore.get(
    "/object-types/{code}/objects/{id}",
    summary="通用对象详情：单个对象的属性值（同一套绑定和行级范围）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def explore_get(code: str, id: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, bd = await _bind(session, code)
    if bd is None:
        raise NotFound("该对象类型未接入数据", details={"objectType": code, "id": id})
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    r = (await session.execute((await bd.stmt(viewer)).where(bd.id_col == id))).mappings().first()
    if r is None:
        # 范围外和不存在分开报：不带范围再查一次（只看有没有，不返回值）
        any_ = select(bd.id_col).select_from(bd.frm).where(bd.id_col == id)
        for k, v in (bd.b.get("where") or {}).items():
            any_ = any_.where(bd.base.c[k] == v)
        hit = (await session.execute(any_)).first()
        if hit is not None:
            raise OutOfScope(details={"objectType": code, "id": id})
        raise NotFound("对象不存在", details={"objectType": code, "id": id})
    return {**_item(bd, r), "titleProp": bd.title, "bound": True, "boundProps": list(bd.cols)}


class ExportIn(ExploreIn):
    ids: list[str] | None = Field(None, description="只导出选中的对象（仍按范围和筛选裁剪）")
    props: list[str] | None = Field(
        None, description="导出哪些属性（默认全部已接入属性）；敏感属性一律不导出"
    )


EXPORT_MAX = 10000


@explore.post(
    "/object-types/{code}/export",
    summary="通用对象导出（CSV，UTF-8 BOM；首列标题属性，敏感属性不导出）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def explore_export(
    code: str, body: ExportIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep
):
    t, bd = await _bind(session, code)
    if ctx.preview_as:
        raise ExportForbidden(details={"reason": "预览中不能导出"})
    if bd is None:
        raise NotFound("该对象类型未接入数据", details={"objectType": code})
    bd.check(body.filters, "筛选")
    keys = [k for k in (body.props or list(bd.cols)) if k in bd.cols and not bd.props[k].is_sensitive]
    keys = [k for k in keys if k != bd.title]
    _, _, viewer = await svc.effective_viewer(session, user, ctx)
    stmt = bd.where(await bd.stmt(viewer), body)
    if body.ids:
        stmt = stmt.where(bd.id_col.in_(body.ids))
    rows = (await session.execute(stmt.order_by(bd.id_col).limit(EXPORT_MAX))).mappings().all()
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(
        [
            bd.props[bd.title].display_name if bd.title in bd.props else "ID",
            *[bd.props[k].display_name for k in keys],
        ]
    )
    for r in rows:
        it = _item(bd, r)
        w.writerow([it["title"], *["" if it["values"][k] is None else it["values"][k] for k in keys]])
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
    stmt = bd.where(await bd.stmt(viewer), body).order_by(bd.id_col).limit(limit)
    return [str(r["__id"]) for r in (await session.execute(stmt)).mappings().all()]
