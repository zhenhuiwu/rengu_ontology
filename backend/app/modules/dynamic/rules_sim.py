"""情景推演示意模型（照搬 src/mock/scenarios.ts simulate / stagePlanOf，
逐位一致，测试对 derived/simulate.json）。

只是演示用的示意计算（价格弹性 −1.6、促销拉动、补货降低缺货）；EXTENSION POINT (PDE): 换成真实推演引擎。
JS 语义：seeded() 线性同余；Math.round = floor(x + 0.5)；toFixed 按 double 的十进制展开四舍五入。
"""

import json
import math
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Any

ELASTICITY = -1.6
BASE_DRIVERS: dict[str, Any] = {
    "pricePct": 0,
    "replenishPct": 0,
    "safetyDays": 7,
    "promo": "无",
    "promoDepth": 0,
    "promoWeeks": 0,
}
_COMMON = Path(__file__).resolve().parents[3] / "seeds" / "json" / "common"


@cache
def _stores() -> list[dict[str, Any]]:
    # 前端 STORES = ALL_STORES 前 12 家核心门店
    return json.loads((_COMMON / "ALL_STORES.json").read_text(encoding="utf-8"))[:12]


@cache
def _products() -> list[dict[str, Any]]:
    return json.loads((_COMMON / "PRODUCTS.json").read_text(encoding="utf-8"))


def seeded(seed: int):
    s = seed & 0xFFFFFFFF

    def nxt() -> float:
        nonlocal s
        s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
        return s / 4294967296

    return nxt


def js_round(x: float) -> int:
    return math.floor(x + 0.5)


def to_fixed(x: float, n: int) -> float:
    q = Decimal(1).scaleb(-n)
    return float(Decimal(x).quantize(q, rounding=ROUND_HALF_UP))


def to_fixed_str(x: float, n: int) -> str:
    q = Decimal(1).scaleb(-n)
    return str(Decimal(x).quantize(q, rounding=ROUND_HALF_UP))


def _num(x: float) -> float | int:
    """JSON 里 JS 的整数 double 写成整数（21 而不是 21.0）。"""
    return int(x) if float(x).is_integer() else x


def _cat(c: str) -> str:
    return c.split("/")[0]


def simulate(scope: dict[str, Any], d: dict[str, Any], ran_at: str) -> dict[str, Any]:
    r = seeded(len(scope["area"]) * 31 + len(scope["category"]) * 7 + scope["weeks"])
    stores = [
        s
        for s in _stores()
        if s["status"] != "已闭店" and (scope["area"] == "华东大区全部" or s["region"] == scope["area"])
    ]
    prods = [
        p for p in _products() if scope["category"] == "全部品类" or _cat(p["category"]) == scope["category"]
    ]
    weeks_n = scope["weeks"]
    price_vol = math.pow(1 + d["pricePct"] / 100, ELASTICITY)
    promo_share = min(d["promoWeeks"], weeks_n) / weeks_n
    no_promo = d["promo"] == "无"
    mult = 1.4 if d["promo"] == "会员券" else 1.9 if d["promo"] == "折扣" else 1.6
    promo_lift = 0 if no_promo else (d["promoDepth"] / 100) * mult * promo_share
    stock_gain = max(-0.5, d["replenishPct"] / 100) * 0.35 + (d["safetyDays"] - 7) * 0.012
    base_stockout = 0.064
    stockout = max(0.008, base_stockout * (1 - stock_gain * 1.6))
    vol_factor = price_vol * (1 + promo_lift) * ((1 - stockout) / (1 - base_stockout))
    sales_factor = vol_factor * (1 + d["pricePct"] / 100)
    base_rate = 0.226
    rate = (
        base_rate
        + (d["pricePct"] / 100) * 0.8
        - (0 if no_promo else (d["promoDepth"] / 100) * 0.55 * promo_share)
        - max(0, d["replenishPct"]) / 100 * 0.012
    )
    scale = len(stores) * len(prods) * 820
    weeks: list[str] = []
    base_sales: list[int] = []
    sales: list[int] = []
    base_margin: list[float] = []
    margin: list[float] = []
    for i in range(weeks_n):
        weeks.append(f"W{i + 1}")
        b = js_round(scale * (0.92 + r() * 0.16) * (1.12 if i in (2, 6) else 1))
        promo_on = not no_promo and i < d["promoWeeks"]
        f = (
            sales_factor * (1 + (d["promoDepth"] / 100) * 0.9) / (1 + promo_lift)
            if promo_on
            else sales_factor / (1 + promo_lift) * (1 + promo_lift * 0.15)
        )
        base_sales.append(b)
        sales.append(js_round(b * f))
        base_margin.append(to_fixed(base_rate * 100, 1))
        extra = (
            -(d["promoDepth"] / 100) * 0.4
            if promo_on
            else (0 if no_promo else (d["promoDepth"] / 100) * 0.2 * promo_share)
        )
        margin.append(to_fixed((rate + extra) * 100, 1))
    b_s = sum(base_sales)
    s_s = sum(sales)
    b_gp = b_s * base_rate
    s_gp = 0.0
    for i, v in enumerate(sales):
        s_gp = s_gp + v * margin[i] / 100
    promo_cost = 0 if no_promo else s_s * (d["promoDepth"] / 100) * promo_share * 0.35
    turn_base = 28.4
    turn = (
        turn_base * (1 + d["replenishPct"] / 100 * 0.45 + (d["safetyDays"] - 7) * 0.03) / max(0.6, vol_factor)
    )
    kpis = [
        {
            "key": "sales",
            "label": "销售额",
            "unit": "万元",
            "base": b_s / 1e4,
            "value": s_s / 1e4,
            "better": "up",
        },
        {
            "key": "gp",
            "label": "毛利额",
            "unit": "万元",
            "base": b_gp / 1e4,
            "value": s_gp / 1e4,
            "better": "up",
        },
        {
            "key": "rate",
            "label": "毛利率",
            "unit": "%",
            "base": base_rate * 100,
            "value": (s_gp / s_s) * 100,
            "better": "up",
        },
        {
            "key": "stockout",
            "label": "缺货率",
            "unit": "%",
            "base": base_stockout * 100,
            "value": stockout * 100,
            "better": "down",
        },
        {
            "key": "turn",
            "label": "库存周转天数",
            "unit": "天",
            "base": turn_base,
            "value": turn,
            "better": "down",
        },
        {
            "key": "promo",
            "label": "促销成本",
            "unit": "万元",
            "base": 0,
            "value": promo_cost / 1e4,
            "better": "down",
        },
    ]
    for k in kpis:
        k["base"], k["value"] = _num(k["base"]), _num(k["value"])
    impacts: list[dict[str, Any]] = []
    for p in prods:
        for s in stores:
            bs = js_round(scale / (len(stores) * len(prods)) * weeks_n * (0.5 + r()))
            k = sales_factor * (0.85 + r() * 0.3)
            so0 = to_fixed(base_stockout * 100 * (0.4 + r() * 1.6), 1)
            so1 = to_fixed(so0 * stockout / base_stockout, 1)
            if d["pricePct"] != 0 and abs(k - 1) > 0.04:
                suggest = "发起调价审批（降价）" if d["pricePct"] < 0 else "发起调价审批（提价）"
            elif so1 > 6 or (d["replenishPct"] > 0 and so0 - so1 > 2):
                suggest = "补货申请"
            else:
                suggest = "—"
            impacts.append(
                {
                    "sku": p["sku"],
                    "name": p["name"],
                    "category": _cat(p["category"]),
                    "storeId": s["id"],
                    "storeName": s["name"],
                    "baseSales": bs,
                    "sales": js_round(bs * k),
                    "stockoutBase": _num(so0),
                    "stockout": _num(so1),
                    "suggest": suggest,
                }
            )
    impacts.sort(key=lambda x: -abs(x["sales"] - x["baseSales"]))  # JS sort 稳定，Python 也稳定
    return {
        "ranAt": ran_at,
        "weeks": weeks,
        "baseSales": base_sales,
        "sales": sales,
        "baseMargin": [_num(x) for x in base_margin],
        "margin": [_num(x) for x in margin],
        "kpis": kpis,
        "impacts": impacts,
    }


def fmt_pct(v: float) -> str:
    return f"{'+' if v >= 0 else '−'}{to_fixed_str(abs(v * 100), 1)}%"


def _avg(a: list[float]) -> float:
    return sum(a) / len(a) if a else 0


def stage_plan(scope: dict[str, Any], d: dict[str, Any], res: dict[str, Any] | None) -> list[dict[str, Any]]:
    """stagePlanOf：转为暂存动作的建议清单（只暂存，不执行）。"""
    out: list[dict[str, Any]] = []
    if not res:
        return out
    by_product: dict[str, list[dict[str, Any]]] = {}
    for x in res["impacts"]:
        by_product.setdefault(x["sku"], []).append(x)
    area = "华东大区" if scope["area"] == "华东大区全部" else scope["area"]
    if d["pricePct"] != 0:
        for sku, rows in list(by_product.items())[:6]:
            ratio = sum(x["sales"] for x in rows) / sum(x["baseSales"] for x in rows) - 1
            out.append(
                {
                    "key": f"price:{sku}",
                    "action": "发起调价审批",
                    "objKind": "product",
                    "objId": sku,
                    "objLabel": rows[0]["name"],
                    "params": f"建议{'降价' if d['pricePct'] < 0 else '提价'} "
                    f"{_num(abs(d['pricePct']))}% · {area} "
                    f"{len(rows)} 家门店 · {scope['weeks']} 周",
                    "reason": f"方案销售额 {fmt_pct(ratio)}",
                }
            )
    if d["replenishPct"] > 0 or d["safetyDays"] > 7:
        hit = [
            (sku, rows) for sku, rows in by_product.items() if any(x["suggest"] == "补货申请" for x in rows)
        ]
        for sku, rows in hit[:6]:
            n = sum(1 for x in rows if x["suggest"] == "补货申请")
            before = to_fixed_str(_avg([x["stockoutBase"] for x in rows]), 1)
            after = to_fixed_str(_avg([x["stockout"] for x in rows]), 1)
            out.append(
                {
                    "key": f"stock:{sku}",
                    "action": "补货申请",
                    "objKind": "product",
                    "objId": sku,
                    "objLabel": rows[0]["name"],
                    "params": f"补货量 +{_num(d['replenishPct'])}% · "
                    f"安全库存 {_num(d['safetyDays'])} 天 · {n} 家门店",
                    "reason": f"缺货率 {before}% → {after}%",
                }
            )
    if d["promo"] != "无" and d["promoDepth"] > 0:
        cost = next(k for k in res["kpis"] if k["key"] == "promo")["value"]
        out.append(
            {
                "key": "promo",
                "action": "发放复购召回优惠券",
                "objKind": "region",
                "objId": "REG-EC",
                "objLabel": "华东大区",
                "params": f"{d['promo']} {_num(d['promoDepth'])}% · {_num(d['promoWeeks'])} 周 · "
                f"范围 {area} · "
                f"{scope['category']}",
                "reason": f"促销成本约 {to_fixed_str(cost, 1)} 万元",
            }
        )
    return out
