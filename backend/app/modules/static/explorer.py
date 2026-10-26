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
from datetime import date, datetime
from decimal import Decimal
from enum import Enum as PyEnum
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Integer, Numeric, and_, func, select
from sqlalchemy import Enum as SaEnum
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.row_filter import apply_row_filter
from app.common.sql_guard import GovernedTable, governed_tables
from app.core.config import get_settings
from app.core.errors import AppError, NotFound
from app.modules.static.models import Metric, ObjectType, Property

Obj = dict[str, Any]

NUMERIC = {"decimal", "integer"}
TEMPORAL = {"date", "timestamp"}
RANGE_TYPES = NUMERIC | TEMPORAL
VISIBILITY = ("prominent", "normal", "hidden")
DISPLAY_FIELDS = {
    "visibility": "visibility",
    "searchable": "searchable",
    "filterable": "filterable",
    "sortable": "sortable",
    "listed": "list_visible",
    "listWidth": "list_width",
    "format": "format",
    "unit": "unit",
    "order": "seq",
}

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
    if p.get("sensitive"):  # 敏感属性不做分面（取值会泄露明文），PDE 定
        out["filterable"] = False
    return out


@lru_cache
def _seed_orders(path: Path) -> dict[str, dict[str, int]]:
    props = json.loads(path.read_text("utf-8")) if path.is_file() else {}
    return {obj: {p["api"]: n for n, p in enumerate(rows)} for obj, rows in props.items()}


def snapshot_flags(obj: str, p: Obj, order: int) -> Obj:
    """旧快照缺少显示标记时只用初始定义补齐，不能借用当前表的标记。"""
    initial_order = _seed_orders(get_settings().seeds_json_dir / "static" / "OBJECT_PROPS.json")
    out = {**default_flags(obj, p), "seq": initial_order.get(obj, {}).get(p["api"], order)}
    out.update({col: p[key] for key, col in DISPLAY_FIELDS.items() if key in p})
    if out["visibility"] == "hidden":
        out.update(filterable=False, sortable=False, list_visible=False)
    if p.get("sensitive"):
        out["filterable"] = False
    return out


def metric_variants(metrics: list[Obj]) -> dict[str, list[Obj]]:
    """只读显式变体；来源和目标都必须在同一版本中已发布，元数据取目标定义。"""
    published = {m["id"]: m for m in metrics if m.get("status") == "已发布"}
    out: dict[str, list[Obj]] = {}
    for m in metrics:
        if m.get("id") not in published:
            continue
        out[m["id"]] = []
        for v in m.get("variants") or []:
            if not isinstance(v, dict) or not v.get("label"):
                continue
            target = published.get(v.get("code"))
            if target is not None:
                out[m["id"]].append(
                    {
                        "code": target["id"],
                        "label": v["label"],
                        "default": bool(v.get("default")),
                        "api": target["api"],
                        "version": target["version"],
                    }
                )
    return out


async def metric_variant_index(session: AsyncSession, ver: str | None = None) -> dict[str, list[Obj]]:
    """公开口径索引；历史快照缺字段时返回空，不借用当前定义。"""
    from app.modules.static.shapes import metric_list_item
    from app.modules.versioning.repo import ReleaseRepo

    if ver:
        release = await ReleaseRepo(session).by_ver_or_404(ver)
        metrics = list(((release.snapshot or {}).get("metrics") or {}).values())
    else:
        metrics = [metric_list_item(m) for m in await session.scalars(select(Metric).order_by(Metric.seq))]
    names = {m["id"]: m["name"] for m in metrics}
    idx: dict[str, dict[str, Obj]] = {}
    for variants in metric_variants(metrics).values():
        for v in variants:
            idx.setdefault(v["label"], {})[v["code"]] = {**v, "name": names[v["code"]]}
    return {label: list(items.values()) for label, items in idx.items()}


# ---------------------------------------------------------------- 输出形状


def prop_meta(p: Property) -> Obj:
    return {
        "key": p.api_name,
        "name": p.display_name,
        "dataType": p.data_type,
        "group": p.prop_group,
        "searchable": p.searchable,
        "filterable": facetable(p),
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
        "facetKind": facet_kind(p) if facetable(p) else None,
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

        if "\x00" in code:  # PG 文本不收 NUL，进库会 500
            raise AppError(
                "对象类型编码不合法",
                code="VALIDATION_FAILED",
                status_code=422,
                details={"fields": [{"field": "code", "message": "不能包含 NUL 字符"}]},
            )
        return await StaticRepo(self.session).object_type(code)

    async def meta(self, code: str, ver: str | None = None) -> Obj:
        from app.modules.static.shapes import metric_list_item

        if ver:
            return await self._historical_meta(code, ver)
        o = await self._object(code)
        props = [p for p in o.properties]
        title = next((p.api_name for p in props if p.is_title), None)
        visible = [p for p in props if p.visibility != "hidden"]
        searchable = [p.display_name for p in visible if p.searchable][:3]
        dim_names = {p.display_name for p in props if p.is_dimension}
        all_metrics = list(await self.session.scalars(select(Metric).order_by(Metric.seq)))
        variants = metric_variants([metric_list_item(m) for m in all_metrics])
        metrics = [
            m
            for m in all_metrics
            if m.code in variants and (dim_names & set(m.dims or []) or o.display_name in (m.dims or []))
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
                {"id": m.code, "name": m.display_name, "unit": m.unit, "variants": variants.get(m.code, [])}
                for m in metrics
            ],
        }

    async def _historical_meta(self, code: str, ver: str) -> Obj:
        from app.modules.versioning.repo import ReleaseRepo

        release = await ReleaseRepo(self.session).by_ver_or_404(ver)
        snapshot = release.snapshot or {}
        obj = (snapshot.get("objectTypes") or {}).get(code)
        if obj is None:
            raise NotFound("该版本中对象类型不存在", details={"id": code, "ver": ver})
        props = [
            Property(
                api_name=api,
                display_name=p["name"],
                data_type=p["type"],
                prop_group=p["group"],
                is_pk=bool(p.get("pk")),
                is_title=bool(p.get("title")),
                is_dimension=bool(p.get("dim")),
                is_sensitive=bool(p.get("sensitive")),
                enum_name=p.get("enumName"),
                **snapshot_flags(code, p, n),
            )
            for n, (api, p) in enumerate((snapshot.get("properties", {}).get(code) or {}).items())
        ]
        props.sort(key=lambda p: (p.seq, p.api_name))
        visible = [p for p in props if p.visibility != "hidden"]
        dim_names = {p.display_name for p in props if p.is_dimension}
        metrics = list((snapshot.get("metrics") or {}).values())
        variants = metric_variants(metrics)
        return {
            "id": code,
            "api": obj["api"],
            "name": obj["name"],
            "titleProp": next((p.api_name for p in props if p.is_title), None),
            "pkProp": next((p.api_name for p in props if p.is_pk), None),
            "kwPlaceholder": " / ".join([p.display_name for p in visible if p.searchable][:3]),
            "properties": [prop_meta(p) for p in visible],
            "hiddenCount": len(props) - len(visible),
            "sampleQuestions": [q.get("q") for q in obj.get("qs", []) if isinstance(q, dict)],
            "hasData": self._table(code) is not None,
            "metrics": [
                {
                    "id": m["id"],
                    "name": m["name"],
                    "unit": m.get("unit"),
                    "variants": variants.get(m["id"], []),
                }
                for m in metrics
                if m.get("status") == "已发布"
                and (dim_names & set(m.get("dimNames") or []) or obj["name"] in (m.get("dimNames") or []))
            ],
        }

    @staticmethod
    def _table(code: str) -> GovernedTable | None:
        return next((g for g in governed_tables().values() if g.object_type == code), None)

    async def facets(self, code: str, filters: Obj, user: Any) -> list[Obj]:
        o = await self._object(code)
        fprops = [p for p in o.properties if facetable(p)]
        by_key = {p.api_name: p for p in fprops}
        unknown = [k for k in filters if k not in by_key]
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
        # 先整体校验筛选值（不管该属性有没有数据列），任何不合法 → 422，不落到数据库
        conds_by_key = {k: self._cond(by_key[k], cols.get(k), v, k) for k, v in filters.items()}
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
            conds = [c for k, cs in conds_by_key.items() if k != p.api_name for c in cs]
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
    def _cond(p: Property, col: ColumnElement | None, v: Any, key: str) -> list[Any]:
        """一个筛选 → SQL 条件。按属性 / 列类型严格校验，不合法一律 422（不让数据库报 500）。

        枚举类分面（facetKind=enum）只收标量数组；区间类（数值 / 日期）只收 {min, max}。
        """
        values = v.values() if isinstance(v, dict) else v if isinstance(v, list) else [v]
        if any(isinstance(x, str) and "\x00" in x for x in values):
            bad_filter(key, "不能包含 NUL 字符")
        if facet_kind(p) == "enum":
            if not isinstance(v, list) or any(isinstance(x, list | dict) or x is None for x in v):
                bad_filter(key, '枚举类筛选应为取值数组，如 ["营业中"]')
            if col is None:  # 该属性没有数据列：不参与过滤
                return []
            return [col.in_([scalar_filter(col, x, key) for x in v])] if v else []
        if not isinstance(v, dict) or not v or set(v) - {"min", "max"}:
            bad_filter(key, "区间类筛选应为 {min, max}")
        for b in v.values():
            if b is not None and (isinstance(b, bool) or not isinstance(b, str | int | float)):
                bad_filter(key, "min / max 应为数字或日期字符串")
        out = []
        if col is None:  # 该属性没有数据列：不参与过滤
            return []
        bounds = {b: scalar_filter(col, v[b], key) for b in ("min", "max") if v.get(b) is not None}
        if "min" in bounds:
            out.append(col >= bounds["min"])
        if "max" in bounds:
            out.append(col <= bounds["max"])
        return out


def facetable(p: Property) -> bool:
    """可以出分面 / 被筛选：filterable 且不隐藏、不敏感（PDE 定：敏感、隐藏都不返回取值）。"""
    return bool(p.filterable) and p.visibility != "hidden" and not p.is_sensitive


def bad_filter(key: str, msg: str) -> None:
    raise AppError(
        "筛选值格式不对",
        code="VALIDATION_FAILED",
        status_code=422,
        details={"fields": [{"field": f"filters.{key}", "message": msg}]},
    )


FILTER_TEXT_MAX = 64


def scalar_filter(col: ColumnElement, x: Any, key: str) -> Any:
    """单个筛选值按列类型转换并校验：枚举须是已知取值，布尔须是 true/false，数值有限且在列范围内。"""
    if isinstance(x, str):
        if len(x) > FILTER_TEXT_MAX:
            bad_filter(key, "值过长（最多 64 字符）")
        if "\x00" in x:
            bad_filter(key, "不能包含 NUL 字符")
        try:
            x.encode("utf-8")
        except UnicodeEncodeError:
            bad_filter(key, "含无效 Unicode 字符")
    if isinstance(col.type, SaEnum) and col.type.enum_class is not None:
        hit = next((m for m in col.type.enum_class if isinstance(x, str) and x in (m.value, m.name)), None)
        if hit is None:
            bad_filter(key, f"未知取值 {x!r}")
        return hit
    try:
        py = col.type.python_type
    except NotImplementedError:
        py = str
    if py is bool:
        if not isinstance(x, bool):
            bad_filter(key, "应为 true / false")
        return x
    if isinstance(x, bool):
        bad_filter(key, "类型不对")
    if py in (datetime, date):
        if not isinstance(x, str):
            bad_filter(key, "日期应为 ISO 字符串，如 2026-10-01")
        return _coerce(col, x, key)
    if py in (Decimal, int, float):
        if not isinstance(x, str | int | float):
            bad_filter(key, "应为数字")
        if py is int and isinstance(x, float) and not x.is_integer():
            bad_filter(key, "应为整数")
        try:
            n = _coerce(col, x, key) if isinstance(x, str) else py(x)
        except (ArithmeticError, ValueError):
            bad_filter(key, "数值不合法")
        _check_range(col, n, key)
        return n
    if not isinstance(x, str):
        bad_filter(key, "应为字符串")
    return x


_INT_LIMITS = {"SmallInteger": 2**15, "Integer": 2**31, "BigInteger": 2**63}


def _check_range(col: ColumnElement, n: Any, key: str) -> None:
    try:
        d = Decimal(n) if not isinstance(n, float) else Decimal(repr(n))
    except (ArithmeticError, ValueError):
        bad_filter(key, "数值不合法")
    if not d.is_finite():
        bad_filter(key, "数值超出范围")
    t = col.type
    lim = next((v for k, v in _INT_LIMITS.items() if type(t).__name__ == k), None)
    if lim is None and isinstance(t, Integer):
        lim = 2**31
    if lim is not None and not (-lim <= d < lim):
        bad_filter(key, "数值超出范围")
    prec, scale = getattr(t, "precision", None), getattr(t, "scale", None)
    if isinstance(t, Numeric) and prec and abs(d) >= Decimal(10) ** (prec - (scale or 0)):
        bad_filter(key, "数值超出范围")
    if abs(d) >= Decimal("1e300"):
        bad_filter(key, "数值超出范围")


def _coerce(col: ColumnElement, x: str, key: str) -> Any:
    """字符串筛选值按列类型转换（日期 / 时间 / 数值列，asyncpg 不收字符串）。"""
    try:
        py = col.type.python_type
    except NotImplementedError:
        return x
    if len(x) > 64:
        bad_filter(key, "值过长")
    try:
        if py is datetime:
            d = datetime.fromisoformat(x)
            return d if d.tzinfo else d.replace(tzinfo=ZoneInfo(get_settings().display_tz))
        if py is date:
            return date.fromisoformat(x[:10])
        if py in (Decimal, int, float):
            n = py(x)
            if py is not int and not Decimal(str(n)).is_finite():
                raise ValueError("非有限数值")
            return n
    except (ValueError, ArithmeticError) as e:
        # 不把 Python 异常原文给前端，按类型给中文提示
        hint = {
            datetime: "应为日期时间，如 2026-10-01T08:00:00",
            date: "应为日期，如 2026-10-01",
            int: "应为整数",
        }
        raise AppError(
            "筛选值格式不对",
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": f"filters.{key}", "message": hint.get(py, "应为数值")}]},
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
