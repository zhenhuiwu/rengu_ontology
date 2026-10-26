"""旧对象 API O2–O7、O9：只读取数据库业务记录，并保留既有行级过滤。

没有已配置数据源的销售比较、历史分析、预测和动作发起明确未接入；不补造演示记录。
"""

import csv
import io
from collections.abc import Callable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
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


# ---------------------------------------------------------------- 数值格式化


def _half_up(v: float, d: int) -> Decimal:
    # JS toLocaleString / toFixed 按 double 的精确值取最近、并列取大，Decimal(v) 是精确值
    return Decimal(v).quantize(Decimal(1).scaleb(-d), rounding=ROUND_HALF_UP)


def fmt_money(v: float | None, d: int = 2) -> str:
    return EMPTY if v is None else f"{_half_up(v, d):,.{d}f}"


def fmt_int(v: float | None) -> str:
    return EMPTY if v is None else f"{floor(v + 0.5):,}"


def fmt_pct(v: float | None, d: int = 2) -> str:
    return EMPTY if v is None else f"{_half_up(v * 100, d):.{d}f}%"


def js_num(v: Any) -> Any:
    """JS 的 String(number)：整数值不带 .0。"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


# ---------------------------------------------------------------- 常量（seeds/json/qa、static）

KIND_LABEL: dict[str, str] = catalog.load("KIND_LABEL")


class DataNotConfigured(AppError):
    status_code, code, message = 503, "DATA_NOT_CONFIGURED", "该分析尚未配置真实数据源"


# ---------------------------------------------------------------- 取数（套行级范围）


class Data:
    """一次请求内的数据库门店/商品/订单，保持旧API字段并按行级范围裁剪，懒加载。"""

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
            "sales": None,
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
    return await _scope_label(data, kind)


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
    # 通用搜索页的筛选（explore_api.ExploreIn 形状）：给了且没给 ids 时，按它在范围内取 id 再走 ids 分支
    query: dict[str, Any] | None = None


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
    if kind not in KINDS:
        raise DataNotConfigured("该对象类型尚未配置范围分析", details={"kind": kind})
    if not sc.single and sc.ids is None and sc.query is not None:
        from app.modules.qa.explore_api import ExploreIn, explore_ids

        ids = await explore_ids(session, viewer, kind, ExploreIn(**sc.query))
        sc = sc.model_copy(update={"ids": ids, "conds": sc.conds or []})
    if sc.single:
        picked = [await _find(data, kind, sc.single.id)]
        conds = sc.conds or []
    elif sc.ids is not None:
        by_id = {ID_OF[kind](x): x for x in await data.rows(kind)}
        picked = [by_id[i] for i in dict.fromkeys(sc.ids) if i in by_id]
        conds = sc.conds if sc.conds is not None else [f"已选 {len(sc.ids)} 个"]
    else:
        _, picked, _ = await _filtered(data, await _type(session, kind), sc.filter or FilterIn())
        conds = (
            sc.conds if sc.conds is not None else _conds(await _type(session, kind), sc.filter or FilterIn())
        )
    basis = (
        f"范围：{sc.label}{' · ' + '、'.join(conds) if conds else ''} · 已按行级权限过滤"
        " · 当前数据库可见记录的基表汇总，未绑定指标口径"
    )
    question = body.question.strip()
    if kind == "order" and question in ("合计", "实付金额合计", "订单汇总"):
        total = sum(o["amount"] for o in picked)
        shipped = sum(1 for o in picked if o["shippedAt"])
        return {
            "text": f"当前可见的 {fmt_int(len(picked))} 单实付金额合计 {fmt_money(total)} 元（未扣退款），"
            f"已出库或签收 {fmt_int(shipped)} 单。",
            "basis": basis,
            "rows": [],
        }
    if kind == "product" and question in ("商品汇总", "平均零售价"):
        cats = {p["category"].split("/")[0] for p in picked}
        avg = sum(p["price"] for p in picked) / len(picked) if picked else None
        return {
            "text": f"当前可见的 {fmt_int(len(picked))} 个商品覆盖 {len(cats)} 个一级品类，"
            f"平均零售价 {fmt_money(avg)} 元。",
            "basis": basis,
            "rows": [],
        }
    raise DataNotConfigured(
        "范围问答、销售比较、历史与预测分析尚未配置，未生成分析结果",
        details={"kind": kind, "capability": "scoped_analysis", "visibleCount": len(picked)},
    )


# ------------------------------------------------ O5 详情（objectRegistry.resolveObject 移植）


def _f(label: str, value: Any) -> dict:
    return {"label": label, "value": value if value is not None else EMPTY}


async def _resolve(data: Data, kind: str, oid: str) -> dict:
    """→ ObjView（与前端 objectRegistry 同形）。不存在 404，范围外 403 OUT_OF_SCOPE。"""
    if kind == "region":
        raise DataNotConfigured("大区业务对象尚未配置真实数据源", details={"kind": kind, "id": oid})
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
                _f("可见订单数", fmt_int(len(orders))),
            ],
            "kpis": [
                {
                    "label": "实付金额",
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
            "timeline": [],
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
    if o["shippedAt"]:
        tl.append({"time": o["shippedAt"], "text": f"已出库（{o['warehouse']}）", "status": "done"})
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
    if found:
        raise OutOfScope(details={"kind": kind, "id": oid})
    raise NotFound("对象不存在", details={"kind": kind, "id": oid})


def _sname(stores: dict[str, dict], sid: str) -> str:
    return stores[sid]["name"] if sid in stores else EMPTY


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
        kpis = [k | {"caliber": None} for k in v.get("kpis") or []]
        out |= {
            "kpis": kpis[:5],
            "quickQuestions": [],
            "scopeLabel": await _scope_label(data, kind),
            "analyticsConfigured": False,
            "analyticsNote": "仅显示基表字段与可见订单汇总；指标口径、历史分析与预测尚未配置",
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
        s = await _find(data, "store", oid)
        peers = [
            {"kind": "store", "id": x["id"], "label": x["name"], "note": f"{x['format']} · {x['status']}"}
            for x in await data.stores()
            if x["city"] == s["city"] and x["id"] != s["id"] and x["name"] != s["name"]
        ]
        groups.append({"key": "peer", "title": "同城门店", "items": peers})
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
        for it in sorted(v.get("timeline") or [], key=lambda x: x["time"], reverse=True)
    ]
    pages = max(1, -(-len(items) // size))
    p = min(page, pages)
    return {
        "items": items[(p - 1) * size : p * size],
        "total": len(items),
        "page": p,
        "size": size,
        "unknownLabel": "结果未知 · 待对账",
        "configured": bool(items),
        "note": "仅展示数据库已有时间字段" if items else "对象历史记录尚未配置",
    }


# ---------------------------------------------------------------- O9 可执行动作


@objects.get("/objects/{kind}/{oid}/actions", summary="O9 对象可执行动作")
async def object_actions(kind: str, oid: str, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    _, viewer = await _viewer(session, user, ctx)
    data = Data(session, viewer)
    await _resolve(data, kind, oid)
    # 当前没有对象级动作发起/鉴权接线，不返回演示审批人、参数或可执行状态。
    return []
