"""通用对象浏览（Object Explorer）元数据：属性显示标记、指标口径变体、分面取值。

依据 docs/backend/generic-object-explorer.md §4/§5；接口形状按 PDE 群里定的（覆盖设计稿的 explorer-meta）：
- GET /object-types/{id}/meta   → {titleProp, properties:[{key,name,dataType,searchable,filterable,sortable,
  listed,order,visibility,format,unit,...}], metrics:[{id,name,unit,variants}]}
- GET /object-types/{id}/facets?filters=<json> → [{key,name,kind:'enum',values:[{value,count}]} |
  {key,name,kind:'range',min,max}]

页面不写任何业务字段：本模块也不写——对象类型 → 数据表走 sql_guard 已登记的受控表
（governed_tables()，按对象类型 code 找），属性 → 列走 property.backing_column（只认表里真有的列）。
没有受控表或属性没配 backing_column 的分面：values=[] / min=max=None（数据不在库里，见 handoff/static.md）。
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy import Enum as SaEnum
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.row_filter import apply_row_filter
from app.common.sql_guard import GovernedTable, governed_tables
from app.core.config import get_settings
from app.core.errors import AppError
from app.modules.static.models import Metric, ObjectType, Property

Obj = dict[str, Any]

NUMERIC = {"decimal", "integer"}
TEMPORAL = {"date", "timestamp"}
RANGE_TYPES = NUMERIC | TEMPORAL
VISIBILITY = ("prominent", "normal", "hidden")

SEED_FILE = Path(__file__).parent / "seeds" / "explorer_meta.json"


# ---------------------------------------------------------------- seed：属性标记 / 口径变体


def _seed_props() -> dict[str, dict[str, Obj]]:
    return json.loads(SEED_FILE.read_text("utf-8"))["properties"]


def default_flags(obj: str, p: Obj) -> Obj:
    """一条属性的标记初始值（p 是 OBJECT_PROPS 的一行）。

    通用默认：主键 / 标题 prominent 且可搜索；数值和日期可排序（设计稿 §4）；整数 format=int。
    seed 文件里登记过的对象类型只按文件开 filterable / list_visible（和前端现有分面、列一致）；
    没登记的类型给一套能用的默认：枚举 / 布尔可筛选，主键 + 维度属性列表展示。
    """
    seeded = _seed_props()
    typ = p["type"]
    key_prop = bool(p.get("pk") or p.get("title"))
    out: Obj = {
        "visibility": "prominent" if key_prop else "normal",
        "searchable": key_prop,
        "filterable": False,
        "sortable": typ in RANGE_TYPES,
        "list_visible": False,
        "list_width": None,
        "format": "int" if typ == "integer" else None,
        "unit": None,
        "backing_column": None,
    }
    if obj in seeded:
        over = dict(seeded[obj].get(p["api"], {}))
        if "backing" in over:
            out["backing_column"] = over.pop("backing")
        out.update(over)
    else:
        out["filterable"] = typ in {"enum", "boolean"}
        out["list_visible"] = bool(p.get("pk") or p.get("dim"))
    if out["visibility"] == "hidden":  # 设计稿 §4 约束：hidden 时这三项无效
        out.update(filterable=False, sortable=False, list_visible=False)
    return out


_SUFFIX = re.compile(r"^(.*?)（([^（）]+)）$")


def metric_variants(metrics: list[Obj]) -> dict[str, list[Obj]]:
    """口径变体：显示名去掉末尾括号后同名的指标互为变体（销售额（不含税）/ 销售额（含税））。

    按名字结构推，不写任何具体口径词；组内第一个（METRIC_LIST 顺序）为默认。只有一个的指标 → []。
    """
    groups: dict[str, list[Obj]] = {}
    for m in metrics:
        hit = _SUFFIX.match(m["name"])
        if hit:
            groups.setdefault(hit.group(1), []).append({**m, "_label": hit.group(2)})
    out: dict[str, list[Obj]] = {}
    for items in groups.values():
        if len(items) < 2:
            continue
        vs = [
            {
                "code": x["id"],
                "label": x["_label"],
                "api": x["api"],
                "version": x["version"],
                "default": i == 0,
            }
            for i, x in enumerate(items)
        ]
        for x in items:
            out[x["id"]] = vs
    return out


# ---------------------------------------------------------------- 输出形状


def prop_meta(p: Property) -> Obj:
    return {
        "key": p.api_name,
        "name": p.display_name,
        "dataType": p.data_type,
        "group": p.prop_group,
        "searchable": p.searchable,
        "filterable": p.filterable,
        "sortable": p.sortable,
        "listed": p.list_visible,
        "listWidth": p.list_width,
        "order": p.seq,
        "visibility": p.visibility,
        "format": p.format,
        "unit": p.unit,
        "pk": p.is_pk,
        "title": p.is_title,
        "sensitive": p.is_sensitive,
        "enumName": p.enum_name,
        "facetKind": facet_kind(p) if p.filterable else None,
    }


def facet_kind(p: Property) -> str:
    return "range" if p.data_type in RANGE_TYPES else "enum"


def _jsonable(v: Any) -> Any:
    if isinstance(v, PyEnum):
        return v.value
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


# ---------------------------------------------------------------- 服务


class ExplorerService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _object(self, code: str) -> ObjectType:
        from app.modules.static.repo import StaticRepo

        return await StaticRepo(self.session).object_type(code)

    async def meta(self, code: str) -> Obj:
        o = await self._object(code)
        props = [p for p in o.properties]
        title = next((p.api_name for p in props if p.is_title), None)
        visible = [p for p in props if p.visibility != "hidden"]
        searchable = [p.display_name for p in visible if p.searchable][:3]
        dim_names = {p.display_name for p in props if p.is_dimension}
        metrics = [
            m
            for m in (await self.session.scalars(select(Metric).order_by(Metric.seq))).all()
            if dim_names & set(m.dims or []) or o.display_name in (m.dims or [])
        ]
        return {
            "id": o.code,
            "api": o.api_name,
            "name": o.display_name,
            "titleProp": title,
            "pkProp": next((p.api_name for p in props if p.is_pk), None),
            "kwPlaceholder": " / ".join(searchable),
            "properties": [prop_meta(p) for p in visible],
            "hiddenCount": len(props) - len(visible),
            "sampleQuestions": [q.get("q") for q in (o.sample_questions or []) if isinstance(q, dict)],
            "hasData": self._table(o.code) is not None,
            "metrics": [
                {"id": m.code, "name": m.display_name, "unit": m.unit, "variants": m.variants or []}
                for m in metrics
            ],
        }

    @staticmethod
    def _table(code: str) -> GovernedTable | None:
        return next((g for g in governed_tables().values() if g.object_type == code), None)

    async def facets(self, code: str, filters: Obj, user: Any) -> list[Obj]:
        o = await self._object(code)
        fprops = [p for p in o.properties if p.filterable and p.visibility != "hidden"]
        by_key = {p.api_name: p for p in o.properties}
        unknown = [k for k in filters if k not in by_key or not by_key[k].filterable]
        if unknown:
            raise AppError(
                "筛选属性不存在或不可筛选",
                code="VALIDATION_FAILED",
                status_code=422,
                details={"fields": [{"field": f"filters.{k}", "message": "不可筛选"} for k in unknown]},
            )
        g = self._table(o.code)
        cols: dict[str, ColumnElement] = {}
        if g is not None:
            for p in fprops:
                if p.backing_column and p.backing_column in g.table.c:
                    cols[p.api_name] = g.table.c[p.backing_column]
        out = []
        for p in fprops:
            kind = facet_kind(p)
            item: Obj = {"key": p.api_name, "name": p.display_name, "kind": kind, "order": p.seq}
            col = cols.get(p.api_name)
            if col is None or g is None:
                item.update(values=[]) if kind == "enum" else item.update(min=None, max=None)
                item["hasData"] = False
                out.append(item)
                continue
            # 计数尊重其它已选筛选，排除本分面自己的条件（多选分面的常规做法）
            conds = [c for k, v in filters.items() if k != p.api_name for c in self._cond(cols.get(k), v, k)]
            if kind == "enum":
                stmt = select(col, func.count()).select_from(g.table).group_by(col)
            else:
                stmt = select(func.min(col), func.max(col)).select_from(g.table)
            if conds:
                stmt = stmt.where(and_(*conds))
            stmt = await apply_row_filter(stmt, user, o.code, g.columns, g.links or None)
            rows = (await self.session.execute(stmt)).all()
            item["hasData"] = True
            if kind == "enum":
                item["values"] = self._enum_values(col, rows)
            else:
                lo, hi = rows[0] if rows else (None, None)
                item.update(min=_jsonable(lo), max=_jsonable(hi))
            out.append(item)
        return out

    @staticmethod
    def _enum_values(col: ColumnElement, rows: list[Any]) -> list[Obj]:
        enum_cls = col.type.enum_class if isinstance(col.type, SaEnum) else None
        vals = [{"value": _jsonable(v), "count": n} for v, n in rows if v is not None]
        if enum_cls is not None:  # 枚举按定义顺序（设计稿 §3：枚举值顺序取枚举定义）
            order = {m.value: i for i, m in enumerate(enum_cls)}
            return sorted(vals, key=lambda x: order.get(x["value"], 1 << 30))
        return sorted(vals, key=lambda x: (-x["count"], str(x["value"])))

    @staticmethod
    def _cond(col: ColumnElement | None, v: Any, key: str) -> list[Any]:
        if col is None:  # 该属性没有数据列：不参与过滤（宁可多给，不报错）
            return []
        enum_cls = col.type.enum_class if isinstance(col.type, SaEnum) else None

        def to_db(x: Any) -> Any:
            if isinstance(x, str) and enum_cls is None:
                x = _coerce(col, x, key)
            if enum_cls is not None:
                hit = next((m for m in enum_cls if x in (m.value, m.name)), None)
                return hit if hit is not None else x
            return x

        if isinstance(v, list):
            return [col.in_([to_db(x) for x in v])] if v else []
        if isinstance(v, dict):
            out = []
            if v.get("min") is not None:
                out.append(col >= to_db(v["min"]))
            if v.get("max") is not None:
                out.append(col <= to_db(v["max"]))
            return out
        if isinstance(v, str | int | float | bool):
            return [col == to_db(v)]
        raise AppError(
            "筛选值格式不对",
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": f"filters.{key}", "message": "应为数组、{min,max} 或单个值"}]},
        )


def _coerce(col: ColumnElement, x: str, key: str) -> Any:
    """字符串筛选值按列类型转换（日期 / 时间 / 数值列，asyncpg 不收字符串）。"""
    try:
        py = col.type.python_type
    except NotImplementedError:
        return x
    try:
        if py is datetime:
            d = datetime.fromisoformat(x)
            return d if d.tzinfo else d.replace(tzinfo=ZoneInfo(get_settings().display_tz))
        if py is date:
            return date.fromisoformat(x[:10])
        if py in (Decimal, int, float):
            return py(x)
    except (ValueError, ArithmeticError) as e:
        raise AppError(
            "筛选值格式不对",
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": f"filters.{key}", "message": str(e)}]},
        ) from e
    return x


def parse_filters(raw: str | None) -> Obj:
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except ValueError:
        v = None
    if not isinstance(v, dict):
        raise AppError(
            "filters 应为 JSON 对象",
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": "filters", "message": '应为 JSON 对象，如 {"status":["营业中"]}'}]},
        )
    return v
