"""ORM 行 → 前端 mock 形状的 dict（键名与 mock 完全一致，mock 里可选、没有值的键不输出）。

接口和快照节都用这里的函数，保证「接口返回的记录 == mock 记录」。时间按 mock 原样格式：
到分钟 "YYYY-MM-DD HH:mm"；METRICS.updatedAt 带秒。数值：整数输出 int，否则 float。
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.modules.static.explorer import DISPLAY_FIELDS
from app.modules.static.models import (
    Dimension,
    Knowledge,
    LinkQuality,
    LinkType,
    Metric,
    ObjectType,
    ObjectView,
    Property,
)

Obj = dict[str, Any]

# mock/objectProps.ts:372 SOURCE_DB（dbOf）
SOURCE_DB = {
    "POS 交易库（华东）": "Oracle 19c",
    "会员中心": "MySQL 8.0",
    "门店主数据": "达梦 DM8 · Oracle 兼容模式",
    "商品主数据": "达梦 DM8 · Oracle 兼容模式",
    "促销引擎": "MySQL 8.0",
    "供应链 WMS 库": "SQL Server 2019",
    "用友 NC 采购": "Oracle 19c",
    "物流运单": "MySQL 8.0",
    "HR 人事库": "达梦 DM8 · Oracle 兼容模式",
}


def db_of(source: str | None) -> str:
    return (source and SOURCE_DB.get(source)) or "--"


def fmt_dt(v: datetime | None, seconds: bool = False) -> str | None:
    if v is None:
        return None
    tz = ZoneInfo(get_settings().display_tz)
    v = v if v.tzinfo else v.replace(tzinfo=tz)
    return v.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S" if seconds else "%Y-%m-%d %H:%M")


def num(v: Decimal | float | int | None) -> int | float | None:
    if v is None:
        return None
    d = Decimal(str(v))
    return int(d) if d == d.to_integral_value() else float(d)


def opt(d: Obj, **kw: Any) -> Obj:
    """只在值不是 None 时加键（mock 的可选字段）。"""
    d.update({k: v for k, v in kw.items() if v is not None})
    return d


# ---------------------------------------------------------------- 对象类型


def conflict_text(o: ObjectType) -> str:
    base = o.conflict_strategy.value
    return f"{base}（{o.conflict_note}）" if o.conflict_note else base


def object_meta(o: ObjectType) -> Obj:
    """mock objectMeta(id, name, group) 的结果。"""
    return {
        "domain": o.domain,
        "desc": o.description,
        "conflict": conflict_text(o),
        "lifecycle": o.lifecycle.value,
        "sync": o.sync,
        "qs": list(o.sample_questions),
        "cards": o.qa_cards,
        "qa30": o.qa30,
        "unused": o.unused_props,
    }


def object_brief(o: ObjectType) -> Obj:
    """Q_OBJECT_TYPES 的一项。"""
    return opt(
        {"key": o.code, "name": o.display_name, "api": o.api_name, "group": o.obj_group},
        source=o.datasource_name,
        count=o.record_count,
        owner=o.owner,
        pk=o.pk_property,
        cutoff=fmt_dt(o.data_cutoff),
    )


def object_link_obj(o: ObjectType) -> Obj:
    """LINK_OBJECTS 的一项。"""
    return opt(
        {"id": o.code, "name": o.display_name, "api": o.api_name, "group": o.obj_group}, table=o.source_table
    )


def object_list_item(o: ObjectType, n_props: int) -> Obj:
    """对象类型列表（ObjectTypes.vue TYPES：id/name/api/group/props/count/status）+ LINK_OBJECTS.table。

    [推断] props = 属性表里的条数（页内常量 TYPES.props 未导出）；count 按 TYPES 写成千分位字符串。
    """
    d = object_link_obj(o)
    d.update(
        props=n_props,
        count=f"{o.record_count:,}" if o.record_count is not None else "--",
        status=o.status.value,
    )
    return d


def prop(p: Property) -> Obj:
    """OBJECT_PROPS 的一项（ObjProp）。"""
    d: Obj = {
        "api": p.api_name,
        "name": p.display_name,
        "type": p.data_type,
        "group": p.prop_group,
        "source": p.source_column,
        "desc": p.description,
        **{key: getattr(p, col) for key, col in DISPLAY_FIELDS.items()},
    }
    for flag, v in (
        ("pk", p.is_pk),
        ("title", p.is_title),
        ("dim", p.is_dimension),
        ("sensitive", p.is_sensitive),
    ):
        if v:
            d[flag] = True
    return opt(d, enumName=p.enum_name)


def mapping(p: Property) -> Obj:
    """字段映射页一行（ObjectTypes.vue MAP_ALL）。"""
    return {
        "prop": p.display_name,
        "col": p.map_column or p.source_column,
        "srcType": p.src_type or p.data_type,
        "transform": p.transform,
    }


def view(v: ObjectView, object_code: str) -> Obj:
    return {
        "id": v.code,
        "objectId": object_code,
        "name": v.name,
        "kind": v.kind.value,
        "owner": v.owner,
        "visibility": v.visibility.value,
        "visibleTo": v.visible_to,
        "lastUsed": fmt_dt(v.last_used_at),
        "uses30d": v.uses_30d,
        "filter": v.filter_text,
        "props": list(v.props),
    }


# ---------------------------------------------------------------- 关系类型


def quality(q: LinkQuality) -> Obj:
    return opt(
        {
            "hit": num(q.hit),
            "orphans": q.orphans,
            "nulls": q.nulls,
            "dup": q.dup,
            "checkedAt": fmt_dt(q.checked_at),
        },
        note=q.note,
        orphanSamples=q.orphan_samples,
    )


def link(lt: LinkType, *, with_runtime: bool = True) -> Obj:
    """LINKS 的一项。with_runtime=False 时不含 quality / usage（快照节只放定义）。"""
    d = opt({"id": lt.code, "name": lt.display_name}, api=lt.api_name)
    d.update(
        {
            "from": lt.from_code,
            "to": lt.to_code,
            "card": lt.cardinality.value,
            "key": lt.key_expr,
            "fromProp": lt.from_prop,
            "toProp": lt.to_prop,
            "join": lt.join_sql,
            "reverse": lt.reverse_name,
            "nullable": lt.nullable,
            "status": lt.status.value,
            "owner": lt.owner,
            "desc": lt.description,
            "history": list(lt.history),
        }
    )
    opt(d, since=lt.since_release, via=lt.via, cr=lt.cr_code)
    if with_runtime:
        d["usage"] = list(lt.usage)
        if lt.quality is not None:
            d["quality"] = quality(lt.quality)
    return d


# ---------------------------------------------------------------- 指标


def metric_list_item(m: Metric) -> Obj:
    """METRIC_LIST 的一项。"""
    from app.modules.static.metric_versions import exec_def

    d = {
        "id": m.code,
        "name": m.display_name,
        "api": m.api_name,
        "group": m.metric_group,
        "unit": m.unit,
        "version": m.version,
        "status": m.status.value,
        "owner": m.owner,
        "variants": list(m.variants or []),
        "defaultRange": exec_def(m.def_rows)["defaultRange"],
    }
    if m.review_cr:
        d["review"] = {"cr": m.review_cr, "note": m.review_note or ""}
    return d


def metric_stat(m: Metric) -> Obj:
    """METRICS 的一项（有统计值的 7 个指标；mock/metrics.ts Metric）。"""
    return {
        "id": m.code,
        "name": m.display_name,
        "formula": m.formula,
        "unit": m.unit,
        "owner": m.owner,
        "version": m.version,
        "status": m.status.value,
        "dims": list(m.dims or []),
        "value": num(m.value),
        "prev": num(m.prev),
        "updatedAt": fmt_dt(m.stat_updated_at, seconds=True),
    }


def metric_detail(m: Metric) -> Obj:
    """指标详情：METRIC_LIST 项 + 定义（METRIC_DEF）+ 允许的维度（METRIC_DIMS）+ 不允许的维度（DENY）。

    [推断] 有统计值的指标附 formula / dimNames（METRICS.dims）/ value / prev / updatedAt；
    `dims` 按 api-index 指允许维度表的行（页面 METRIC_DIMS 原样字段 dim/hier/obj/def/note）。
    """
    d = metric_list_item(m)
    from app.modules.static.metric_defaults import range_label

    d["def"] = [
        {**row, "v": range_label(row.get("v"))} if row.get("k") == "默认时间范围" else dict(row)
        for row in m.def_rows if isinstance(row, dict)
    ]
    # 执行口径（时间字段 / 默认过滤），定义表单按此回填，不解析展示行。
    from app.modules.static.metric_versions import exec_def

    d["execDef"] = exec_def(m.def_rows)
    d["dims"] = list(m.dim_rows)
    d["disallowed"] = list(m.disallowed_rows)
    if m.formula is not None:
        s = metric_stat(m)
        d.update(
            formula=s["formula"],
            dimNames=s["dims"],
            value=s["value"],
            prev=s["prev"],
            updatedAt=s["updatedAt"],
        )
    return d


# ---------------------------------------------------------------- 维度与码表


def dim_level(lv: Any) -> Obj:
    return opt(
        {"name": lv.name, "api": lv.api_name, "source": lv.source, "count": lv.value_count},
        note=lv.note,
    )


def dim_value(v: Any) -> Obj:
    d = {
        "key": v.value_key,
        "code": v.code,
        "name": v.name,
        "level": v.level_name,
        "status": v.status.value,
        "synonyms": list(v.synonyms),
    }
    return opt(
        d,
        parent=v.parent,
        count=v.record_count,
        src=v.src,
        note=v.note,
        ref=v.ref,
        since=v.since_release,
        until=v.until_release,
        draft=v.draft_cr,
        diff=v.diff,
        pub=v.pub,
    )


def dim_mapping(m: Any) -> Obj:
    d = {
        "key": m.map_key,
        "phrase": m.phrase,
        "targets": list(m.targets),
        "match": m.match.value,
        "source": m.source.value,
        "hits": m.hits,
    }
    return opt(d, conflict=m.conflict, resolve=m.resolve, draft=m.draft_cr, diff=m.diff)


def dimension(d: Dimension, values: list[Any], mappings: list[Any], *, with_runtime: bool = True) -> Obj:
    """DIM_ITEMS 的一项（含 values / mappings）。with_runtime=False 时不含 usage。"""
    out: Obj = {
        "id": d.code,
        "name": d.display_name,
        "api": d.api_name,
        "kind": d.kind.value,
        "group": d.dim_group,
        "status": d.status.value,
        "owner": d.owner,
        "object": d.object.code if d.object is not None else "",
        "objectName": d.object_name if d.object is not None else "",
        "attr": d.attr,
        "column": d.source_column,
        "valueSrc": d.value_src,
        "sync": d.sync,
        "desc": d.description,
        "levels": [dim_level(x) for x in d.levels],
        "qa": dict(d.qa),
        "values": [dim_value(v) for v in values],
        "mappings": [dim_mapping(m) for m in mappings],
        "metricDims": list(d.metric_dims),
        "history": list(d.history),
        "since": d.since_release,
    }
    if with_runtime:
        out["usage"] = list(d.usage)
    return opt(
        out,
        metricNote=d.metric_note,
        levelsBefore=d.levels_before,
        countLabel=d.count_label,
        valuesNote=d.values_note,
        draftNote=d.draft_note,
    )


# ---------------------------------------------------------------- 业务知识


def knowledge(k: Knowledge, *, with_runtime: bool = True) -> Obj:
    """KNOWLEDGE 的一项（KEntry）。with_runtime=False 时不含 refs。"""
    d: Obj = {
        "id": k.code,
        "name": k.name,
        "type": k.type.value,
        "status": k.status.value,
        "updatedAt": fmt_dt(k.content_updated_at),
        "owner": k.owner,
        "definition": k.definition,
        "calc": k.calc,
        "synonyms": list(k.synonyms),
        "applies": [
            {"kind": a.target_kind.value, "id": a.target_code, "name": a.target_name} for a in k.applies
        ],
        "period": k.period,
        "maps": k.maps,
        "filters": list(k.filters),
        "tries": list(k.tries),
        "history": list(k.history),
    }
    if with_runtime:
        d["refs"] = list(k.refs)
    return opt(
        d, since=k.since_release, periods=k.periods, cr=k.cr_code, draft=k.draft, stoppedIn=k.stopped_release
    )
