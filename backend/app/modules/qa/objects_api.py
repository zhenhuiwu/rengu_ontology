"""R3 对象搜索 / R4 对象详情：O2–O7、O9（qa-api §5 §6），挂在 qa router 上（router.py 末尾 include）。

数据：门店 / 商品 / 订单直接读 biz 的表（store、product、biz_order、order_line），行级范围走骨架
`apply_row_filter` + biz/governance.py 的登记，问数不自己拼条件。前端 mock 里扩充的 OBJ_EXTRA_STORES
（seeds/json/qa）还没并入 biz 的 ALL_STORES，这里按 mock 的做法追加（按 id 和店名去重）[推断]：
它们不在库里，没法套 SQL 范围，所以只在该用户对门店**不限范围**时才追加（宁严勿宽）。

派生的演示数据（门店 9 月销售额、常售商品、补充时间线）按 mock `objects-qa.ts` 的算法逐行移植
（同一个线性同余随机数 `seeded`、同一个 `hash`，常售商品的乱序照 V8 Array.prototype.sort 的
二分插入排序调用顺序复现），所以和前端演示数据一致。没有 store_sales 表，销售额仍是演示值 [推断]。
"""

import csv
import io
import json
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from math import floor
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy import select

from app.common.audit import audit
from app.common.auth import CurrentUser, CurrentUserDep, require_func, row_filter
from app.common.context import ViewContextDep, readonly_extra
from app.common.pagination import MAX_PAGE_SIZE
from app.common.row_filter import apply_row_filter, is_deny_all
from app.core.config import get_settings
from app.core.errors import AppError, ExportForbidden, NotFound
from app.core.schema import DATETIME_SEC_FORMAT, ApiModel, _tz
from app.db.session import SessionDep
from app.modules.biz.governance import ORDER_COLUMNS, ORDER_LINKS, PRODUCT_COLUMNS, STORE_COLUMNS
from app.modules.biz.models import BizOrder, OrderLine, Product, Store
from app.modules.qa import catalog
from app.modules.qa import repo as r
from app.modules.qa import service as svc

objects = APIRouter(dependencies=[Depends(require_func("object-search", "view"))])

EMPTY = "--"
KINDS = ("store", "product", "order")


class OutOfScope(AppError):
    status_code, code, message = 403, "OUT_OF_SCOPE", "不在你的数据范围内"


# ---------------------------------------------------------------- 与前端一致的格式化 / 随机数


def _half_up(v: float, d: int) -> Decimal:
    # JS toLocaleString / toFixed 按 double 的精确值取最近、并列取大，Decimal(v) 是精确值
    return Decimal(v).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)


def fmt_money(v: float | None, d: int = 2) -> str:
    return EMPTY if v is None else f"{_half_up(v, d):,.{d}f}"


def fmt_int(v: float | None) -> str:
    return EMPTY if v is None else f"{floor(v + 0.5):,}"


def fmt_pct(v: float | None, d: int = 2) -> str:
    return EMPTY if v is None else f"{_half_up(v * 100, d):.{d}f}%"


def js_round1(v: float) -> float:
    """Math.round(v * 10) / 10。"""
    return floor(v * 10 + 0.5) / 10


def js_num(v: Any) -> Any:
    """JS 的 String(number)：整数值不带 .0。"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def seeded(seed: int) -> Callable[[], float]:
    s = seed & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal s
        s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
        return s / 4294967296

    return nxt


def js_hash(s: str) -> int:
    a = 7
    for ch in s:
        a = (a * 31 + ord(ch)) & 0xFFFFFFFF
    return a


def v8_random_sort(items: list[Any], rnd: Callable[[], float]) -> list[Any]:
    """`[...items].sort(() => r() - 0.5)` 在 V8（TimSort，长度 < 64 时整段二分插入排序）下的结果。"""
    a = list(items)
    n = len(a)
    if n < 2:
        return a
    # CountAndMakeRun(0, n)
    run = 2
    desc = (rnd() - 0.5) < 0
    for _ in range(2, n):
        o = rnd() - 0.5
        if (desc and o >= 0) or (not desc and o < 0):
            break
        run += 1
    if desc:
        a[:run] = a[:run][::-1]
    # BinaryInsertionSort(0, run, n)
    for start in range(run, n):
        left, right, pivot = 0, start, a[start]
        while left < right:
            mid = left + ((right - left) >> 1)
            if rnd() - 0.5 < 0:
                right = mid
            else:
                left = mid + 1
        a[left + 1 : start + 1] = a[left:start]
        a[left] = pivot
    return a


def fmt_time_ms(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, _tz()).strftime("%Y-%m-%d %H:%M")


def _ms(s: str) -> float:
    return datetime.fromisoformat(s).timestamp() * 1000


# ---------------------------------------------------------------- 常量（seeds/json/qa、static）

KIND_LABEL: dict[str, str] = catalog.load("KIND_LABEL")


@lru_cache
def _metrics() -> dict[str, dict[str, Any]]:
    path = get_settings().seeds_json_dir / "static" / "METRICS.json"
    return {m["id"]: m for m in json.loads(path.read_text(encoding="utf-8"))}


def _metric(code: str) -> dict[str, Any] | None:
    return _metrics().get(code)


def _cal(code: str) -> str | None:
    m = _metric(code)
    return f"{m['name']} {m['version']}" if m else None


def caliber_of(label: str) -> str | None:
    qm = catalog.load("QA_METRIC")
    if label.startswith("复购客户销售额（不含税）"):
        return f"{qm['ex']['name']} {qm['ex']['version']}"
    if label.startswith("复购客户销售额（含税）"):
        return f"{qm['in']['name']} {qm['in']['version']}"
    for pre, code in (
        ("销售额（不含税）", "gmv"),
        ("销售额（含税）", "gmv_tax"),
        ("实付金额（含税）", "gmv_tax"),
        ("订单数", "order_cnt"),
        ("客单价", "atv"),
        ("出库率", "ship_ontime"),
    ):
        if label.startswith(pre):
            return _cal(code)
    return None


def store_sales(sid: str) -> tuple[float, float]:
    for q in catalog.load("QA_EC_STORES"):
        if q["id"] == sid:
            return q["sales"], q["mom"]
    rnd = seeded(js_hash(sid))
    sales = js_round1(5 + rnd() * 14)
    return sales, js_round1(-15 + rnd() * 22)


def _mom(m: float) -> str:
    return f"{'+' if m > 0 else ''}{_half_up(m, 1)}%"


# ---------------------------------------------------------------- 取数（套行级范围）


class Data:
    """一次请求内的门店 / 商品 / 订单（mock 形状的 dict），按查看人的行级范围裁剪，懒加载。"""

    def __init__(self, session: Any, viewer: CurrentUser) -> None:
        self.session, self.viewer = session, viewer
        self._c: dict[str, Any] = {}

    async def expr(self, kind: str) -> dict | None:
        key = f"expr:{kind}"
        if key not in self._c:
            self._c[key] = await row_filter(self.viewer, kind)
        return self._c[key]

    async def restricted(self, kind: str) -> bool:
        return await self.expr(kind) is not None

    async def denied(self, kind: str) -> bool:
        return is_deny_all(await self.expr(kind))

    async def all_stores(self) -> dict[str, dict]:
        """门店名查找表（不裁剪，只用来显示订单的门店名）。"""
        if "all_stores" not in self._c:
            rows = (await self.session.execute(select(Store).order_by(Store.id))).scalars().all()
            out = {s.code: _store_row(s) for s in rows}
            names = {s["name"] for s in out.values()}
            for s in catalog.load("OBJ_EXTRA_STORES"):
                if s["id"] not in out and s["name"] not in names:
                    out[s["id"]] = dict(s)
            self._c["all_stores"] = out
        return self._c["all_stores"]

    async def stores(self) -> list[dict]:
        if "stores" not in self._c:
            stmt = await apply_row_filter(
                select(Store).where(Store.is_test.is_(False)).order_by(Store.id),
                self.viewer,
                "store",
                STORE_COLUMNS,
            )
            out = [_store_row(s) for s in (await self.session.execute(stmt)).scalars().all()]
            if not await self.restricted("store"):
                ids, names = {s["id"] for s in out}, {s["name"] for s in out}
                out += [
                    dict(s)
                    for s in catalog.load("OBJ_EXTRA_STORES")
                    if s["id"] not in ids and s["name"] not in names
                ]
            self._c["stores"] = out
        return self._c["stores"]

    async def products(self) -> list[dict]:
        if "products" not in self._c:
            stmt = await apply_row_filter(
                select(Product).order_by(Product.id), self.viewer, "product", PRODUCT_COLUMNS
            )
            self._c["products"] = [
                _product_row(p) for p in (await self.session.execute(stmt)).scalars().all()
            ]
        return self._c["products"]

    async def orders(self) -> list[dict]:
        if "orders" not in self._c:
            stmt = select(BizOrder, Store.code).join(Store, Store.id == BizOrder.store_id)
            stmt = stmt.where(BizOrder.is_test.is_(False)).order_by(BizOrder.id)
            stmt = await apply_row_filter(stmt, self.viewer, "order", ORDER_COLUMNS, ORDER_LINKS)
            rows = (await self.session.execute(stmt)).all()
            lines: dict[int, list[dict]] = {}
            if rows:
                ls = select(OrderLine.order_id, Product.code, OrderLine.qty, OrderLine.price)
                ls = ls.join(Product, Product.id == OrderLine.product_id)
                ls = ls.where(OrderLine.order_id.in_([o.id for o, _ in rows]))
                for oid, sku, qty, price in (
                    await self.session.execute(ls.order_by(OrderLine.order_id, OrderLine.line_no))
                ).all():
                    lines.setdefault(oid, []).append(
                        {"sku": sku, "qty": js_num(float(qty)), "price": float(price)}
                    )
            self._c["orders"] = [_order_row(o, code, lines.get(o.id, [])) for o, code in rows]
        return self._c["orders"]

    async def rows(self, kind: str) -> list[dict]:
        return await {"store": self.stores, "product": self.products, "order": self.orders}[kind]()


def _local(dt: datetime | None) -> str | None:
    return dt.astimezone(_tz()).strftime(DATETIME_SEC_FORMAT) if dt else None


def _store_row(s: Store) -> dict:
    return {
        "id": s.code,
        "code": s.store_no,
        "name": s.name,
        "city": s.city,
        "region": s.region,
        "format": s.format,
        "manager": s.manager,
        "area": float(s.area) if s.area is not None else None,
        "openedAt": s.opened_at.isoformat(),
        "status": s.status.value,
    }


def _product_row(p: Product) -> dict:
    return {
        "sku": p.code,
        "barcode": p.barcode,
        "name": p.name,
        "spec": p.spec,
        "category": p.category,
        "brand": p.brand,
        "price": float(p.price),
        "unit": p.unit,
    }


def _order_row(o: BizOrder, store_code: str, lines: list[dict]) -> dict:
    return {
        "id": o.code,
        "storeId": store_code,
        "channel": o.channel,
        "status": o.status.value,
        "createdAt": _local(o.created_at_biz),
        "shippedAt": _local(o.shipped_at),
        "amount": float(o.amount),
        "discount": float(o.discount or 0),
        "warehouse": o.warehouse,
        "waybill": o.waybill,
        "member": o.member_phone,
        "lines": lines,
    }


# ------------------------------------------------ 搜索类型定义（取值部分；标签 / 顺序来自 O1）

ID_OF = {"store": lambda x: x["id"], "product": lambda x: x["sku"], "order": lambda x: x["id"]}
TITLE_OF = {"store": lambda x: x["name"], "product": lambda x: x["name"], "order": lambda x: x["id"]}
FACET_GET: dict[str, dict[str, Callable[[dict], str]]] = {
    "store": {
        "region": lambda s: s["region"],
        "city": lambda s: s["city"],
        "format": lambda s: s["format"],
        "status": lambda s: s["status"],
    },
    "product": {"cat": lambda p: p["category"].split("/")[0], "brand": lambda p: p["brand"]},
    "order": {"channel": lambda o: o["channel"], "ostatus": lambda o: o["status"]},
}


def _cells(kind: str, x: dict, stores: dict[str, dict]) -> dict[str, Any]:
    if kind == "store":
        keys = ("code", "city", "region", "format", "manager", "area")
        return {k: x[k] for k in keys} | {
            "sales": store_sales(x["id"])[0],
            "openedAt": x["openedAt"],
            "status": x["status"],
        }
    if kind == "product":
        return {k: x[k] for k in ("sku", "spec", "category", "brand", "price")}
    s = stores.get(x["storeId"])
    return {
        "store": s["name"] if s else EMPTY,
        "channel": x["channel"],
        "amount": x["amount"],
        "createdAt": x["createdAt"],
        "status": x["status"],
    }


def _kw_text(kind: str, x: dict, stores: dict[str, dict]) -> str:
    if kind == "store":
        return f"{x['name']} {x['code']} {x['id']} {x['manager']}"
    if kind == "product":
        return f"{x['name']} {x['sku']} {x['barcode']} {x['brand']}"
    s = stores.get(x["storeId"])
    return f"{x['id']} {s['name'] if s else ''} {x['waybill'] or ''}"


async def _type(session: Any, kind: str) -> Any:
    if kind not in KINDS:
        raise NotFound("对象类型不存在", details={"kind": kind})
    for t in await r.ObjectTypeRepo(session).current():
        if t.kind == kind:
            return t
    raise NotFound("对象类型不存在", details={"kind": kind})


class SearchIn(ApiModel):
    kw: str = ""
    facets: dict[str, list[str]] = Field(default_factory=dict)
    page: int = Field(1, ge=1)
    size: int = Field(16, ge=1, le=MAX_PAGE_SIZE)


class FilterIn(ApiModel):
    kw: str = ""
    facets: dict[str, list[str]] = Field(default_factory=dict)


class ExportIn(FilterIn):
    ids: list[str] | None = None


async def _filtered(data: Data, t: Any, f: FilterIn) -> tuple[list[dict], list[dict], list[dict]]:
    """→ (范围内全部行, 关键词 + 分面命中行, 分面计数)。"""
    kind = t.kind
    scoped = [] if await data.denied(kind) else await data.rows(kind)
    stores = await data.all_stores() if kind == "order" else {}
    kw = f.kw.strip().lower()
    by_kw = [x for x in scoped if not kw or kw in _kw_text(kind, x, stores).lower()]
    getters = FACET_GET[kind]
    sel = {k: v for k, v in f.facets.items() if k in getters and v}

    def match(x: dict, skip: str | None = None) -> bool:
        return all(k == skip or getters[k](x) in v for k, v in sel.items())

    hits = [x for x in by_kw if match(x)]
    facets = []
    for fd in [] if await data.denied(kind) else t.facet_defs or []:
        get = getters.get(fd["key"])
        if get is None:
            continue
        cnt: dict[str, int] = {}
        for x in by_kw:
            if match(x, fd["key"]):
                cnt[get(x)] = cnt.get(get(x), 0) + 1
        allv = dict.fromkeys(fd.get("order") or [])
        for x in scoped:
            allv.setdefault(get(x))
        vals = [{"v": v, "n": cnt.get(v, 0)} for v in allv]
        if not fd.get("order"):
            vals.sort(key=lambda a: (-a["n"], a["v"]))  # [推断] 前端按拼音排同数，这里按码位
        facets.append({"key": fd["key"], "label": fd["label"], "vals": vals})
    return scoped, hits, facets


async def _viewer(session: Any, user: CurrentUser, ctx: Any) -> tuple[Any, CurrentUser]:
    _, u, viewer = await svc.effective_viewer(session, user, ctx)
    return u, viewer


async def _scope_label(data: Data, kind: str) -> str:
    label = KIND_LABEL.get(kind, kind)
    expr = await data.expr(kind if kind in KINDS else "store")
    if expr is None:
        return f"数据范围：全部{label}"
    if is_deny_all(expr):
        return f"无权查看{label}"
    return f"数据范围：{label} 按角色范围裁剪"  # [推断] 真实范围接入后由 row_filter 的表达式生成文字


async def _scope_text(data: Data, kind: str, label: str, ctx: Any, u: Any) -> str:
    if ctx.preview_as:
        sl = await _scope_label(data, kind)
        if await data.denied(kind):
            return f"{u.name} {sl}，结果和计数都为空。"
        return f"{u.name} 的{sl}。范围外的{label}结果和计数都不含。"
    sc = catalog.load("OBJ_SCOPE")
    return f"可见范围：{sc['visible']}。{sc['hidden']}无权限，结果和计数都不含。"


@objects.post(
    "/objects/{kind}/search",
    summary="O2 对象搜索（分面计数不含本分面）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def search(kind: str, body: SearchIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    t = await _type(session, kind)
    u, viewer = await _viewer(session, user, ctx)
    data = Data(session, viewer)
    _, hits, facets = await _filtered(data, t, body)
    stores = await data.all_stores() if kind == "order" else {}
    pages = max(1, -(-len(hits) // body.size))
    page = min(body.page, pages)
    items = [
        {"id": ID_OF[kind](x), "title": TITLE_OF[kind](x), "cells": _cells(kind, x, stores)}
        for x in hits[(page - 1) * body.size : page * body.size]
    ]
    return {
        "items": items,
        "total": len(hits),
        "page": page,
        "size": body.size,
        "facets": facets,
        "scopeText": await _scope_text(data, kind, t.label, ctx, u),
    }


@objects.post(
    "/objects/{kind}/export",
    summary="O3 导出对象（CSV，UTF-8 BOM）",
    openapi_extra=readonly_extra("history", "preview"),
)
async def export(kind: str, body: ExportIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    t = await _type(session, kind)
    if ctx.preview_as:
        raise ExportForbidden(details={"reason": "预览中不能导出"})
    _, viewer = await _viewer(session, user, ctx)
    data = Data(session, viewer)
    _, hits, _ = await _filtered(data, t, body)
    if body.ids:
        want = set(body.ids)
        hits = [x for x in hits if ID_OF[kind](x) in want]
    stores = await data.all_stores() if kind == "order" else {}
    cols = t.column_defs or []
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow([f"{t.label}{'号' if kind == 'order' else '名称'}", *[c["title"] for c in cols]])
    for x in hits:
        cells = _cells(kind, x, stores)
        w.writerow(
            [
                TITLE_OF[kind](x),
                *[js_num(cells.get(c["key"])) if cells.get(c["key"]) is not None else "" for c in cols],
            ]
        )
    name = f"{t.label}_{len(hits)}.csv"
    detail = {"kind": kind, "rows": len(hits), "selected": bool(body.ids)}
    await audit(session, user, action="qa.objects.export", target=kind, detail=detail)
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"},
    )


# ---------------------------------------------------------------- O4 带范围提问


class AskSingle(ApiModel):
    kind: str
    id: str
    title: str = ""


class AskScopeIn(ApiModel):
    kind: str
    label: str = ""
    filter: FilterIn | None = None
    ids: list[str] | None = None
    single: AskSingle | None = None
    conds: list[str] | None = None  # [推断] 前端已算好的条件文字（可不传，后台按 filter 生成）


class ScopedAskIn(ApiModel):
    scope: AskScopeIn
    question: str = Field(min_length=1, max_length=500)


def _conds(t: Any, f: FilterIn) -> list[str]:
    out = [
        f"{fd['label']} = {' / '.join(f.facets[fd['key']])}"
        for fd in t.facet_defs or []
        if f.facets.get(fd["key"])
    ]
    return out + ([f"关键词“{f.kw.strip()}”"] if f.kw.strip() else [])


@objects.post(
    "/scoped-ask",
    summary="O4 带范围提问（规则回答，同 answerFor）",
    openapi_extra=readonly_extra("preview"),
)
async def scoped_ask(body: ScopedAskIn, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    sc = body.scope
    _, viewer = await _viewer(session, user, ctx)
    data = Data(session, viewer)
    kind = sc.single.kind if sc.single else sc.kind
    gmv = _cal("gmv") or "销售额（不含税）"
    cutoff = catalog.load("OBJ_CUTOFF")
    if sc.single:
        conds: list[str] = sc.conds or []
    elif sc.ids:
        conds = sc.conds if sc.conds is not None else [f"已选 {len(sc.ids)} 个"]
    elif sc.filter is not None and kind in KINDS:
        conds = sc.conds if sc.conds is not None else _conds(await _type(session, kind), sc.filter)
    else:
        conds = sc.conds or []
    basis = (
        f"范围：{sc.label}{' · ' + '、'.join(conds) if conds else ''} · 已按行级权限过滤 · 数据截至 {cutoff}"
    )
    if sc.single:
        s1 = sc.single
        if s1.kind == "store":
            stores = await data.stores()
            s = next((x for x in stores if x["id"] == s1.id), None)
            if s is None:
                raise OutOfScope(details={"kind": "store", "id": s1.id})
            sales, mom = store_sales(s1.id)
            peers = sorted(
                ({"x": x, "v": store_sales(x["id"])[0]} for x in stores if x["city"] == s["city"]),
                key=lambda p: -p["v"],
            )
            rank = next(i for i, p in enumerate(peers) if p["x"]["id"] == s1.id) + 1
            return {
                "text": f"2026 年 9 月，{s1.title} {gmv} 为 {fmt_money(sales, 1)} 万元，环比 {_mom(mom)}，"
                f"在{s['city']} {len(peers)} 家门店中排第 {rank}。",
                "basis": basis,
                "rowsTitle": f"{s['city']}门店 · {gmv}（万元）",
                "rows": [
                    {
                        "id": p["x"]["id"],
                        "kind": "store",
                        "name": p["x"]["name"],
                        "value": fmt_money(p["v"], 1),
                        "note": "当前门店" if p["x"]["id"] == s1.id else p["x"]["format"],
                    }
                    for p in peers[:5]
                ],
            }
        return {
            "text": f"已限定在{KIND_LABEL.get(s1.kind, '')}「{s1.title}」上回答：{body.question}。"
            "这类问题需要在问数工作台按明细展开，这里先给出对象的关键指标，见上方概览。",
            "basis": basis,
            "rows": [],
        }
    if kind not in KINDS:
        raise NotFound("对象类型不存在", details={"kind": kind})
    if sc.ids:
        by_id = {ID_OF[kind](x): x for x in await data.rows(kind)}
        picked = [by_id[i] for i in sc.ids if i in by_id]  # 范围外的 id 直接丢掉
    else:
        _, picked, _ = await _filtered(data, await _type(session, kind), sc.filter or FilterIn())
    if kind == "store":
        rows = [(x, *store_sales(x["id"])) for x in picked]
        total = sum(v for _, v, _ in rows)
        down = sum(1 for *_, m in rows if m < 0)
        top = sorted(rows, key=lambda t: -t[1])[:5]
        return {
            "text": f"这 {fmt_int(len(rows))} 家门店 2026 年 9 月{gmv}合计 {fmt_money(total, 1)} 万元，"
            f"其中 {fmt_int(down)} 家环比下降。销售额最高的 5 家见下表。",
            "basis": basis,
            "rowsTitle": f"{gmv}（万元）· 前 5",
            "rows": [
                {
                    "id": x["id"],
                    "kind": "store",
                    "name": x["name"],
                    "value": fmt_money(v, 1),
                    "note": f"{x['city']} · 环比 {_mom(m)}",
                }
                for x, v, m in top
            ],
        }
    if kind == "order":
        total = sum(o["amount"] for o in picked)
        tax = _cal("gmv_tax") or "含税"
        shipped = sum(1 for o in picked if o["shippedAt"])
        return {
            "text": f"这 {fmt_int(len(picked))} 单实付金额合计 {fmt_money(total)} 元（{tax}，未扣退款），"
            f"已出库或签收 {fmt_int(shipped)} 单。",
            "basis": basis,
            "rows": [],
        }
    cats = {p["category"].split("/")[0] for p in picked}
    avg = sum(p["price"] for p in picked) / max(1, len(picked))
    return {
        "text": f"这 {fmt_int(len(picked))} 个商品覆盖 {len(cats)} 个一级品类，"
        f"平均零售价 {fmt_money(avg)} 元。",
        "basis": basis,
        "rows": [],
    }


# ------------------------------------------------ O5 详情（objectRegistry.resolveObject 移植）


def _f(label: str, value: Any) -> dict:
    return {"label": label, "value": value if value is not None else EMPTY}


async def _resolve(data: Data, kind: str, oid: str) -> dict:
    """→ ObjView（与前端 objectRegistry 同形）。不存在 404，范围外 403 OUT_OF_SCOPE。"""
    if kind == "region":
        reg = catalog.load("REGION_EC")
        if oid != reg["id"]:
            raise NotFound("对象不存在", details={"kind": kind, "id": oid})
        if await data.denied("store"):
            raise OutOfScope(details={"kind": kind, "id": oid})
        cover = f"{reg['provinces']} 省 · {reg['stores']} 家门店"
        cust = fmt_int(catalog.load("QA_TOTAL_CUSTOMERS"))
        lv = {"warn": "warn", "error": "error", "unknown": "unknown"}
        return {
            "kind": "region",
            "kindLabel": "大区",
            "id": reg["id"],
            "title": reg["name"],
            "code": reg["id"],
            "status": {"text": reg["status"], "color": "warning"},
            "brief": [
                _f("负责人", reg["owner"]),
                _f("覆盖", cover),
                _f("销售额", f"{reg['sales']}（9 月）"),
                _f("状态", f"{reg['status']}（自 {reg['since']}）"),
            ],
            "fields": [
                _f("对象类型", "大区"),
                _f("编码", reg["id"]),
                _f("负责人", reg["owner"]),
                _f("上级", reg["parent"]),
                _f("覆盖", cover),
                _f("状态", f"{reg['status']}（自 {reg['since']}）"),
                _f("销售额", f"{reg['sales']}（9 月）"),
                _f("复购客户", f"{cust} 人"),
                _f("最近修改", f"{reg['modifiedBy']} · {reg['modifiedAt']}"),
            ],
            "kpis": [
                {"label": "销售额（不含税）", "value": "2,074.2", "unit": "万元", "compare": "9 月"},
                {
                    "label": "复购客户销售额（不含税）",
                    "value": fmt_money(catalog.load("QA_TOTAL_SALES"), 1),
                    "unit": "万元",
                    "compare": "环比 -8.4%（8 月 1,404.0）",
                    "tone": "down",
                },
                {"label": "复购客户", "value": cust, "unit": "人"},
                {"label": "复购占比", "value": "62.0%", "compare": "8 月 63.6%", "tone": "down"},
            ],
            "timeline": [
                {"time": t["time"], "text": f"{t['title']} · {t['desc']}", "status": lv.get(t["level"])}
                for t in catalog.load("QA_REGION_TIMELINE")
            ],
            "related": catalog.load("QA_REGION_RELATED"),
            "extra": {
                "owner": reg["owner"],
                "provinces": reg["provinces"],
                "stores": reg["stores"],
                "sales": reg["sales"],
                "repeatShare": "62.0%",
                "parent": reg["parent"],
            },
        }
    if kind not in KINDS:
        raise NotFound("对象类型不存在", details={"kind": kind})
    if kind == "store":
        s = await _find(data, kind, oid)
        orders = [o for o in await data.orders() if o["storeId"] == s["id"]]
        paid = [o for o in orders if o["status"] not in ("已取消", "待支付")]
        total = sum(o["amount"] for o in paid)
        shipped = sum(1 for o in orders if o["shippedAt"])
        area = EMPTY if s["area"] is None else f"{fmt_money(s['area'], 1)} ㎡"
        return {
            "kind": "store",
            "kindLabel": "门店",
            "id": s["id"],
            "title": s["name"],
            "code": s["code"],
            "status": {"text": s["status"], "color": "default"},
            "brief": [
                _f("区域", s["region"]),
                _f("店长", s["manager"]),
                _f("业态", s["format"]),
                _f("营业面积", area),
            ],
            "fields": [
                _f("门店编码", s["code"]),
                _f("城市", s["city"]),
                _f("区域", s["region"]),
                _f("业态", s["format"]),
                _f("店长", s["manager"]),
                _f("营业面积", area),
                _f("开业日期", s["openedAt"]),
                _f("近 7 日订单", fmt_int(len(orders))),
            ],
            "kpis": [
                {
                    "label": "实付金额（含税）",
                    "value": fmt_money(total),
                    "unit": "元",
                    "compare": f"{fmt_int(len(paid))} 单已支付",
                },
                {"label": "订单数", "value": fmt_int(len(orders)), "unit": "单"},
                {"label": "客单价", "value": fmt_money(total / len(paid)) if paid else EMPTY, "unit": "元"},
                {
                    "label": "出库率",
                    "value": fmt_pct(shipped / len(orders)) if orders else EMPTY,
                    "compare": f"{fmt_int(shipped)} 单已出库或签收",
                },
            ],
            "timeline": [
                {"time": "2026-10-25 06:00", "text": "日结数据已同步"},
                {"time": "2026-10-21 14:32", "text": f"店长由 张卫国 变更为 {s['manager']}"},
                {"time": f"{s['openedAt']} 09:00", "text": "门店开业"},
            ],
            "related": [
                {
                    "kind": "order",
                    "id": o["id"],
                    "label": o["id"],
                    "note": f"{o['status']} · {fmt_money(o['amount'])}",
                }
                for o in orders
            ],
        }
    if kind == "product":
        p = await _find(data, kind, oid)
        price = fmt_money(p["price"])
        return {
            "kind": "product",
            "kindLabel": "商品",
            "id": p["sku"],
            "title": p["name"],
            "code": p["sku"],
            "brief": [
                _f("规格", p["spec"]),
                _f("品类", p["category"]),
                _f("零售价", f"{price} 元/{p['unit']}"),
                _f("条码", p["barcode"]),
            ],
            "fields": [
                _f("SKU", p["sku"]),
                _f("条码", p["barcode"]),
                _f("品牌", p["brand"]),
                _f("规格", p["spec"]),
                _f("品类", p["category"]),
                _f("销售单位", p["unit"]),
                _f("零售价", f"{price} 元"),
            ],
        }
    o = await _find(data, kind, oid)
    s = (await data.all_stores()).get(o["storeId"])
    tl = [{"time": o["createdAt"], "text": f"下单（{o['channel']}）"}]
    if o["status"] not in ("待支付", "已取消"):
        tl.append({"time": o["createdAt"], "text": "支付成功"})
    if o["shippedAt"]:
        tl.append({"time": o["shippedAt"], "text": f"已出库（{o['warehouse']}）", "status": "done"})
    if o["status"] == "已取消":
        tl.append({"time": o["createdAt"], "text": "用户取消", "status": "error"})
    prods = {p["sku"]: p for p in await data.products()}
    sname = s["name"] if s else EMPTY
    return {
        "kind": "order",
        "kindLabel": "订单",
        "id": o["id"],
        "title": o["id"],
        "code": o["channel"],
        "status": {"text": o["status"], "color": "default"},
        "brief": [
            _f("门店", sname),
            _f("实付金额", f"{fmt_money(o['amount'])} 元"),
            _f("下单时间", o["createdAt"]),
            _f("运单号", o["waybill"]),
        ],
        "fields": [
            _f("订单号", o["id"]),
            _f("渠道", o["channel"]),
            _f("门店", sname),
            _f("发货仓", o["warehouse"]),
            _f("会员手机", o["member"]),
            _f("商品件数", fmt_int(len(o["lines"]))),
            _f("优惠金额", fmt_money(o["discount"]) if o["discount"] else EMPTY),
            _f("实付金额", fmt_money(o["amount"])),
            _f("下单时间", o["createdAt"]),
            _f("出库时间", o["shippedAt"]),
            _f("运单号", o["waybill"]),
        ],
        "timeline": tl,
        "related": ([{"kind": "store", "id": s["id"], "label": s["name"], "note": s["code"]}] if s else [])
        + [
            {
                "kind": "product",
                "id": ln["sku"],
                "label": prods[ln["sku"]]["name"] if ln["sku"] in prods else ln["sku"],
                "note": f"× {ln['qty']} · {fmt_money(ln['price'])}",
            }
            for ln in o["lines"]
        ],
    }


async def _find(data: Data, kind: str, oid: str) -> dict:
    for x in await data.rows(kind):
        if ID_OF[kind](x) == oid:
            return x
    # 不在范围内的区分「不存在」和「范围外」
    exists_any = {
        "store": lambda: select(Store.id).where(Store.code == oid),
        "product": lambda: select(Product.id).where(Product.code == oid),
        "order": lambda: select(BizOrder.id).where(BizOrder.code == oid),
    }[kind]()
    found = (await data.session.execute(exists_any.limit(1))).first() is not None
    if kind == "store" and not found:
        found = any(s["id"] == oid for s in catalog.load("OBJ_EXTRA_STORES"))
    if found:
        raise OutOfScope(details={"kind": kind, "id": oid})
    raise NotFound("对象不存在", details={"kind": kind, "id": oid})


def _sname(stores: dict[str, dict], sid: str) -> str:
    return stores[sid]["name"] if sid in stores else EMPTY


def _quick(kind: str, title: str) -> list[str]:
    if kind == "store":
        return [f"{title} 9 月销售额和环比", f"{title} 在同城门店里排第几", f"{title} 近 7 天订单出库情况"]
    if kind == "region":
        return [
            f"{title} 复购客户销售额为什么下降",
            f"{title} 哪些门店拖累最大",
            f"{title} 近 6 个月复购占比",
        ]
    if kind == "product":
        return [f"{title} 近 30 天在哪些门店卖得最好", f"{title} 售价调整前后销量对比"]
    return [f"{title} 为什么还没出库", f"{title} 的会员近 90 天下单记录"]


def _groups(v: dict) -> list[dict]:
    defs = catalog.load("FIELD_GROUPS").get(v["kind"], [])
    used: set[str] = set()
    out = []
    for g in defs:
        fs = []
        for lab in g["labels"]:
            f = next((x for x in v["fields"] if x["label"] == lab), None)
            if f and lab not in used:
                used.add(lab)
                fs.append(f)
        out.append({"title": g["title"], "fields": fs})
    rest = [f for f in v["fields"] if f["label"] not in used]
    if rest:
        out.append({"title": "其他", "fields": rest})
    return [g for g in out if g["fields"]]


@objects.get("/objects/{kind}/{oid}", summary="O5 对象详情（view=drawer|full，合并 /object-views）")
async def object_detail(
    kind: str,
    oid: str,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    view: str = Query("full", pattern="^(drawer|full)$"),
):
    _, viewer = await _viewer(session, user, ctx)
    data = Data(session, viewer)
    v = await _resolve(data, kind, oid)
    out: dict[str, Any] = {
        k: v.get(k) for k in ("kind", "kindLabel", "id", "code", "title", "status", "brief")
    } | {"groups": _groups(v), "fields": v["fields"]}
    out |= v.get("extra") or {}
    if view == "full":
        kpis = [k | {"caliber": caliber_of(k["label"])} for k in v.get("kpis") or []]
        if kind == "store":
            sales, mom = store_sales(oid)
            kpis.insert(
                0,
                {
                    "label": "9 月销售额（不含税）",
                    "value": fmt_money(sales, 1),
                    "unit": "万元",
                    "compare": f"环比 {_mom(mom)}",
                    "tone": "down" if mom < 0 else "up",
                    "caliber": _cal("gmv"),
                },
            )
        out |= {
            "kpis": kpis[:5],
            "quickQuestions": _quick(kind, v["title"]),
            "scopeLabel": await _scope_label(data, kind),
            "cutoff": catalog.load("OBJ_CUTOFF"),
        }
    return {k: x for k, x in out.items() if x is not None}


# ---------------------------------------------------------------- O6 关联对象


async def _visible_ids(data: Data, kind: str) -> set[str] | None:
    """None = 不限；否则是范围内的 id 集合。"""
    if kind not in KINDS and kind != "region":
        return None
    if kind == "region":
        return set() if await data.denied("store") else None
    if not await data.restricted(kind):
        return None
    return {ID_OF[kind](x) for x in await data.rows(kind)}


async def _relation_groups(data: Data, v: dict) -> list[dict]:
    kind, oid = v["kind"], v["id"]
    rel_title = {"order": "订单", "product": "商品", "store": "门店", "region": "大区"}
    groups: list[dict] = []
    if kind == "store":
        reg = catalog.load("REGION_EC")
        s = await _find(data, "store", oid)
        groups.append(
            {
                "key": "region",
                "title": "所属大区",
                "items": [
                    {
                        "kind": "region",
                        "id": reg["id"],
                        "label": reg["name"],
                        "note": f"{s['region']} · 负责人 {reg['owner']}",
                    }
                ],
            }
        )
        peers = [
            {"kind": "store", "id": x["id"], "label": x["name"], "note": f"{x['format']} · {x['status']}"}
            for x in await data.stores()
            if x["city"] == s["city"] and x["id"] != s["id"] and x["name"] != s["name"]
        ]
        groups.append({"key": "peer", "title": "同城门店", "items": peers})
        rnd = seeded(js_hash(oid) + 3)
        top = v8_random_sort(await data.products(), rnd)[:8]
        groups.append(
            {
                "key": "product",
                "title": "常售商品",
                "items": [
                    {
                        "kind": "product",
                        "id": p["sku"],
                        "label": p["name"],
                        "note": f"{p['spec']} · 近 30 天 {fmt_int(floor(200 + rnd() * 1800 + 0.5))} 件",
                    }
                    for p in top
                ],
            }
        )
    by_kind: dict[str, list[dict]] = {}
    for x in v.get("related") or []:
        by_kind.setdefault(x["kind"], []).append(x)
    for k, items in by_kind.items():
        if kind == "region" and k == "store":
            title = "下属门店"
        elif kind == "order" and k == "store":
            title = "下单门店"
        elif kind == "order" and k == "product":
            title = "订单商品"
        else:
            title = rel_title.get(k, k)
        groups.append({"key": f"rel-{k}", "title": title, "items": items})
    if kind == "product":
        stores = await data.all_stores()
        groups.append(
            {
                "key": "orders",
                "title": "包含该商品的订单",
                "items": [
                    {
                        "kind": "order",
                        "id": o["id"],
                        "label": o["id"],
                        "note": f"{_sname(stores, o['storeId'])} · {o['status']}",
                    }
                    for o in await data.orders()
                    if any(ln["sku"] == oid for ln in o["lines"])
                ],
            }
        )
    return groups


@objects.get("/objects/{kind}/{oid}/related", summary="O6 关联对象（按关系分组，每组各自分页）")
async def object_related(
    kind: str,
    oid: str,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    group: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(8, ge=1, le=MAX_PAGE_SIZE),
):
    _, viewer = await _viewer(session, user, ctx)
    data = Data(session, viewer)
    v = await _resolve(data, kind, oid)
    out = []
    for g in await _relation_groups(data, v):
        kinds = {x["kind"] for x in g["items"]}
        if kinds and all([await data.denied(k if k in KINDS else "store") for k in kinds]):
            out.append({"key": g["key"], "title": g["title"], "denied": True, "items": []})
            continue
        p = page if group in (None, g["key"]) else 1
        pages = max(1, -(-len(g["items"]) // size))
        p = min(p, pages)
        items = []
        for x in g["items"][(p - 1) * size : p * size]:
            vis = await _visible_ids(data, x["kind"])
            items.append(x | {"denied": True} if vis is not None and x["id"] not in vis else x)
        out.append(
            {
                "key": g["key"],
                "title": g["title"],
                "items": items,
                "total": len(g["items"]),
                "page": p,
                "size": size,
            }
        )
    if group is not None:
        out = [g for g in out if g["key"] == group] or out
    return out


# ---------------------------------------------------------------- O7 时间线

TL_BY_KIND: dict[str, list[tuple[str | None, str, str]]] = {
    "store": [
        (None, "日结数据已同步", "系统"),
        ("warn", "正常 → 预警", "规则「客流环比预警」触发 · 系统"),
        (None, "预警 → 正常", "规则「客流环比预警」解除 · 系统"),
        (None, "动作成功：变更门店状态", "王敏 发起 · 赵磊 审批"),
        ("error", "动作失败：变更门店状态", "周婷 发起 · 赵磊 审批 · 门店主数据写回超时"),
    ],
    "product": [
        (None, "日结数据已同步", "系统"),
        ("error", "动作失败：门店调价", "王敏 发起 · 用友 ERP 返回 500"),
        (None, "规则命中：库存低于安全线", "规则 R-012 · 系统"),
        ("unknown", "门店调价", "规则 R-007 暂存 · 王敏 确认 · 用友 ERP 超时"),
        (None, "动作成功：补货申请", "规则 R-012 暂存 · 周晓峰 确认"),
    ],
}


def extra_timeline(kind: str, oid: str, base: list[dict]) -> list[dict]:
    if kind in ("region", "order"):
        return base
    rnd = seeded(js_hash(oid) + 11)
    out: list[dict] = []
    t = _ms("2026-10-24T22:00:00+08:00")
    ev = TL_BY_KIND.get(kind, TL_BY_KIND["store"])
    for _ in range(18):
        st, text, desc = ev[floor(rnd() * len(ev))]
        while st == "unknown" and (
            any(o.get("status") == "unknown" for o in out) or any(o.get("status") == "unknown" for o in base)
        ):
            st, text, desc = ev[floor(rnd() * len(ev))]
        out.append({"time": fmt_time_ms(t), "text": text, "desc": desc, "status": st})
        t -= 86400000 * (1 + floor(rnd() * 4)) + 3600000 * floor(rnd() * 9)
    return sorted([*base, *out], key=lambda x: x["time"], reverse=True)


@objects.get("/objects/{kind}/{oid}/timeline", summary="O7 对象时间线")
async def object_timeline(
    kind: str,
    oid: str,
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    page: int = Query(1, ge=1),
    size: int = Query(8, ge=1, le=MAX_PAGE_SIZE),
):
    _, viewer = await _viewer(session, user, ctx)
    v = await _resolve(Data(session, viewer), kind, oid)
    items = [
        {k: x for k, x in it.items() if x is not None}
        for it in extra_timeline(kind, oid, v.get("timeline") or [])
    ]
    pages = max(1, -(-len(items) // size))
    p = min(page, pages)
    return {
        "items": items[(p - 1) * size : p * size],
        "total": len(items),
        "page": p,
        "size": size,
        "unknownLabel": catalog.load("QA_UNKNOWN_LABEL"),
    }


# ---------------------------------------------------------------- O9 可执行动作


@objects.get("/objects/{kind}/{oid}/actions", summary="O9 对象可执行动作")
async def object_actions(kind: str, oid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, viewer = await _viewer(session, user, ctx)
    data = Data(session, viewer)
    v = await _resolve(data, kind, oid)
    acts: list[dict]
    if kind == "store":
        st = v["status"]["text"]
        acts = [
            {
                "key": "status",
                "name": "变更门店状态",
                "desc": f"当前：{st}",
                "fields": [{"label": "调整为", "value": "装修中" if st == "营业中" else "营业中"}],
                "impact": "写回门店主数据，影响线上门店可见性",
                "irreversible": True,
                "approver": "赵磊",
            }
        ]
    elif kind == "region":
        reg = catalog.load("REGION_EC")
        acts = [
            {
                "key": "coupon",
                "name": "发放复购召回优惠券",
                "desc": "影响 3,812 位会员",
                "fields": [
                    {"label": "券模板", "value": "复购召回券 20 元"},
                    {"label": "人群", "value": "华东大区 60 天未复购会员"},
                ],
                "impact": "向 3,812 位会员发券",
                "approver": "赵磊",
            },
            {
                "key": "follow",
                "name": "指派大区经理跟进",
                "desc": f"指派给 {reg['owner']}",
                "fields": [{"label": "执行人", "value": reg["owner"]}],
                "impact": "生成跟进任务，不改业务数据",
            },
            {
                "key": "budget",
                "name": "调整大区预算",
                "desc": "动作类型变更中",
                "fields": [],
                "impact": "调整大区预算科目额度",
                "approver": "赵磊",
                "blocked": "动作类型“调整大区预算”变更中，暂不可发起",
            },
        ]
    elif kind == "product":
        p = await _find(data, "product", oid)
        acts = [
            {
                "key": "price",
                "name": "门店调价",
                "desc": f"当前 {fmt_money(p['price'])} 元",
                "fields": [{"label": "新售价", "value": fmt_money(floor(p["price"] * 95 + 0.5) / 100)}],
                "impact": "写回全部门店价签",
                "irreversible": True,
                "approver": "赵磊",
            },
            {
                "key": "replenish",
                "name": "补货申请",
                "desc": "缺货门店 7 家",
                "fields": [{"label": "门店", "value": "低于安全线的 7 家门店"}],
                "impact": "生成补货单，提交给区域仓",
            },
        ]
    else:
        o = await _find(data, "order", oid)
        out_ = o["status"] in ("已出库", "已签收")
        acts = [
            {
                "key": "reship",
                "name": "补发出库单",
                "desc": "订单已出库" if out_ else "重新下发到仓",
                "fields": [{"label": "发货仓", "value": o["warehouse"] or EMPTY}],
                "impact": "重新生成出库单",
                "blocked": "订单已出库，不能补发" if out_ else None,
            },
            {
                "key": "refund",
                "name": "退款审核",
                "desc": f"实付 {fmt_money(o['amount'])} 元",
                "fields": [
                    {"label": "审核结果", "value": "同意退款"},
                    {"label": "退款金额", "value": fmt_money(o["amount"])},
                ],
                "impact": "同意后原路退回，不可撤销",
                "irreversible": True,
                "approver": "赵磊",
            },
        ]
    # [推断] state / stateTip 来自动态的 action_run（X1/X2），问数不建表：这里先给空，前端按本地状态显示
    return [
        {
            k: x
            for k, x in (a | {"irreversible": a.get("irreversible", False), "state": ""}).items()
            if x is not None
        }
        for a in acts
    ]
