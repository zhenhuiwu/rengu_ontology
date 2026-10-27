"""static 模块 seed：导出文件 → 表（seeds/json/static/，export-mock 的 static 组，原样导入，不修 mock）。

| 导出文件                     | 表                    | 说明 |
|------------------------------|----------------------|------|
| Q_OBJECT_TYPES.json          | object_type          | 18 行；+ derived/objectMeta + LINK_OBJECTS.table |
| Q_OBJECT_TYPES × OBJECT_PROPS | property             | 211 行；映射列按 ObjectTypes.vue 规则算好落库 |
| derived/viewsOf.json         | object_view          | 285 行；业务键 (对象, 视图 id)（视图 id 跨对象重复） |
| LINKS.json                   | link_type            | 27 行；非对象端点只存 code [推断] |
| LINKS.json（quality）        | link_quality         | 26 行（有 quality 的关系） |
| METRIC_LIST.json + METRICS.json | metric               | 15 行；METRIC_DEF 等共用示例存到每个指标 [推断] |
| DIM_ITEMS.json               | dimension            | 17 行 |
| DIM_ITEMS.json（levels）     | dim_level            | 30 行 |
| DIM_ITEMS.json（values）     | dim_value            | 235 行 |
| DIM_ITEMS.json（mappings）   | dim_mapping          | 34 行 |
| KNOWLEDGE.json               | knowledge            | 26 行 |
| KNOWLEDGE.json（applies）    | knowledge_apply      | 56 行 |
| PATH_GROUPS.json（paths）    | onto_qa_path         | 7 行（3 组）；组级 check 存默认路径行 [推断] |
| PATH_GROUPS.json（paths.links）| onto_qa_path_step    | 10 行 |
| PATH_GROUPS.json（examples） | onto_qa_path_example | 9 行；path=-1（先追问）→ path_id 空 |
| CHANGE_REQUESTS.json         | change_request       | 16 行；+ CR_DETAILS（见下）[推断] |

extra/static/CR_DETAILS.json：mock/changes.ts 的 CR_DETAILS（export-mock 还没导出，整版发布的合并校验要用
touches / synonyms / checks / impact），由 vite ssrLoadModule 原样导出；export-mock 补上后改读 json/。

不落表、接口直接读 JSON 常量的：K_TYPES、CARD_API、CONFLICT_RULE。
export-mock 没导出、照抄前端页内常量的（[推断]）：ORDER_MAP_ROWS（objectProps.ts:379）、
Metrics.vue 的 DENY（不允许的维度）、ObjectTypes.vue TYPES 里 purchase 的「草稿」状态。
"""

import json
from typing import Any

from app.core.enums import from_api
from app.core.schema import parse_display_dt
from app.modules.static.explorer import default_flags
from app.modules.static.models import (
    ApplyKind,
    Cardinality,
    ChangeRequest,
    ConflictStrategy,
    CRLevel,
    CRStatus,
    CrTarget,
    DefStatus,
    Dimension,
    DimKind,
    DimLevel,
    DimMapping,
    DimValue,
    DimValueStatus,
    Knowledge,
    KnowledgeApply,
    KnowledgeStatus,
    KnowledgeType,
    Lifecycle,
    LinkQuality,
    LinkType,
    MappingSource,
    MatchMode,
    Metric,
    MetricStatus,
    ObjectStatus,
    ObjectType,
    ObjectView,
    Property,
    QaPath,
    QaPathExample,
    QaPathStep,
    ViewKind,
    ViewVisibility,
)
from app.modules.versioning.models import OntologyRelease
from app.seeds import Ref, Row, SeedContext, SeedSpec, derived_args

JSON_MODULE = "static"

# ObjectTypes.vue:181 —— 映射页的源类型（按属性类型）
SRC_TYPE = {
    "string": "VARCHAR2(64)",
    "enum": "NUMBER(2)",
    "timestamp": "DATE",
    "decimal": "NUMBER(14,2)",
    "integer": "NUMBER(6)",
    "boolean": "CHAR(1)",
    "json": "CLOB",
}
# mock/objectProps.ts:379 ORDER_MAP_ROWS（export-mock 未导出，照抄）[推断]
ORDER_MAP_ROWS = [
    {"prop": "订单号", "col": "order_id", "srcType": "VARCHAR2(32)", "transform": ""},
    {
        "prop": "渠道",
        "col": "channel_code",
        "srcType": "CHAR(2)",
        "transform": "字典映射：01→门店，02→小程序，03→外卖平台，04→社区团购",
    },
    {
        "prop": "订单状态",
        "col": "status",
        "srcType": "NUMBER(2)",
        "transform": "字典映射：10→待支付 … 50→已出库",
    },
    {"prop": "出库时间", "col": "out_time", "srcType": "DATE", "transform": "按 Asia/Shanghai 解释"},
    {"prop": "应付金额（不含税）", "col": "total_amt_notax", "srcType": "NUMBER(14,2)", "transform": ""},
    {
        "prop": "收货电话",
        "col": "recv_mobile",
        "srcType": "VARCHAR2(20)",
        "transform": "敏感字段：中间 4 位脱敏",
    },
    {"prop": "是否测试单", "col": "test_flag", "srcType": "CHAR(1)", "transform": "'Y' → 是，其余 → 否"},
]
# Metrics.vue:52 DENY（页内常量，所有指标共用）[推断]
METRIC_DENY = [
    {"dim": "供应商", "reason": "一个订单行对应多个供应商批次，按供应商拆分会重复计算，请用“采购金额”。"},
    {"dim": "促销活动", "reason": "一单可参加多个活动，金额不可加，请用“活动让利额”。"},
]
# ObjectTypes.vue:79 TYPES 里状态不是「已发布」的对象类型 [推断]
OBJECT_STATUS = {"purchase": ObjectStatus.draft}


def _dt(s: str | None) -> Any:
    return parse_display_dt(s) if s else None


def _index(ctx: SeedContext, file: str, key: str = "id") -> dict[str, Row]:
    return {x[key]: x for x in ctx.load(file)}


# ---------------------------------------------------------------- 对象类型 / 属性 / 视图


def _object_meta(ctx: SeedContext) -> dict[str, Row]:
    return {c["args"][0]: c["result"] for c in ctx.load("derived/objectMeta.json")}


def split_conflict(text: str) -> tuple[ConflictStrategy, str | None]:
    """'源系统优先（门店主数据为准）' → (latest_source_ts, '门店主数据为准')。"""
    for m in ConflictStrategy:
        if text == m.value:
            return m, None
        if text.startswith(m.value + "（") and text.endswith("）"):
            return m, text[len(m.value) + 1 : -1]
    raise ValueError(f"未知的冲突策略：{text!r}")


def _object_type(r: Row, ctx: SeedContext) -> Row:
    keys = [x["key"] for x in ctx.load("Q_OBJECT_TYPES.json")]
    meta = _object_meta(ctx)[r["key"]]
    link_obj = _index(ctx, "LINK_OBJECTS.json").get(r["key"], {})
    strategy, note = split_conflict(meta["conflict"])
    return {
        "code": r["key"],
        "api_name": r["api"],
        "display_name": r["name"],
        "obj_group": r["group"],
        "domain": meta["domain"],
        "description": meta["desc"],
        "conflict_strategy": strategy,
        "conflict_note": note,
        "lifecycle": from_api(Lifecycle, meta["lifecycle"]),
        "sync": meta["sync"],
        "sample_questions": meta["qs"],
        "qa_cards": meta["cards"],
        "qa30": meta["qa30"],
        "unused_props": meta["unused"],
        "source_table": link_obj.get("table"),
        "datasource_name": r.get("source"),
        "owner": r.get("owner"),
        "pk_property": r.get("pk"),
        "record_count": r.get("count"),
        "data_cutoff": _dt(r.get("cutoff")),
        "status": OBJECT_STATUS.get(r["key"], ObjectStatus.published),
        "seq": keys.index(r["key"]),
    }


def map_row(obj: str, p: Row) -> Row:
    """映射页一行（ObjectTypes.vue:185 MAP_ALL）：order 按 ORDER_MAP_ROWS 覆盖，其余按类型推。"""
    if obj == "order":
        for o in ORDER_MAP_ROWS:
            if o["prop"] == p["name"]:
                return dict(o)
    return {
        "prop": p["name"],
        "col": p["source"],
        "srcType": SRC_TYPE.get(p["type"], "VARCHAR2(64)"),
        "transform": "敏感字段：脱敏显示" if p.get("sensitive") else "",
    }


def _properties(r: Row, ctx: SeedContext) -> list[Row]:
    out = []
    for i, p in enumerate(ctx.load("OBJECT_PROPS.json")[r["key"]]):
        m = map_row(r["key"], p)
        out.append(
            {
                "object_type_id": r["key"],
                "api_name": p["api"],
                "display_name": p["name"],
                "data_type": p["type"],
                "prop_group": p["group"],
                "is_pk": bool(p.get("pk")),
                "is_title": bool(p.get("title")),
                "is_dimension": bool(p.get("dim")),
                "is_sensitive": bool(p.get("sensitive")),
                "enum_name": p.get("enumName"),
                "source_column": p["source"],
                "description": p["desc"],
                "src_type": m["srcType"],
                "transform": m["transform"],
                "map_column": m["col"],
                "seq": i,
                **default_flags(r["key"], p, 1),  # 通用对象浏览标记（explorer.py，static_0004）
            }
        )
    return out


def _view(r: Row, ctx: SeedContext) -> Row:
    obj = derived_args(r)[0]
    rows = next(c["result"] for c in ctx.load("derived/viewsOf.json") if c["args"][0] == obj)
    return {
        "object_type_id": obj,
        "code": r["id"],
        "name": r["name"],
        "kind": from_api(ViewKind, r["kind"]),
        "owner": r["owner"],
        "visibility": from_api(ViewVisibility, r["visibility"]),
        "visible_to": r["visibleTo"],
        "last_used_at": _dt(r["lastUsed"]),
        "uses_30d": r["uses30d"],
        "filter_text": r["filter"],
        "props": r["props"],
        "seq": next(i for i, x in enumerate(rows) if x["id"] == r["id"]),
    }


# ---------------------------------------------------------------- 关系类型


def _link(r: Row, ctx: SeedContext) -> Row:
    objs = {x["key"] for x in ctx.load("Q_OBJECT_TYPES.json")}
    ids = [x["id"] for x in ctx.load("LINKS.json")]
    return {
        "code": r["id"],
        "api_name": r.get("api"),
        "display_name": r["name"],
        "from_code": r["from"],
        "to_code": r["to"],
        "from_object_id": r["from"] if r["from"] in objs else None,
        "to_object_id": r["to"] if r["to"] in objs else None,
        "cardinality": from_api(Cardinality, r["card"]),
        "key_expr": r["key"],
        "from_prop": r["fromProp"],
        "to_prop": r["toProp"],
        "via": r.get("via"),
        "join_sql": r["join"],
        "reverse_name": r["reverse"],
        "nullable": r["nullable"],
        "description": r["desc"],
        "status": from_api(DefStatus, r["status"]),
        "since_release": r.get("since"),
        "cr_code": r.get("cr"),
        "owner": r["owner"],
        "usage": r["usage"],
        "history": r["history"],
        "seq": ids.index(r["id"]),
    }


def _link_quality(r: Row, _ctx: SeedContext) -> Row | None:
    q = r.get("quality")
    if not q:
        return None
    return {
        "link_type_id": r["id"],
        "hit": q["hit"],
        "orphans": q["orphans"],
        "nulls": q["nulls"],
        "dup": q["dup"],
        "checked_at": _dt(q["checkedAt"]),
        "note": q.get("note"),
        "orphan_samples": q.get("orphanSamples"),
    }


# ---------------------------------------------------------------- 指标


def seed_metric_variants(metrics: list[Row]) -> dict[str, list[Row]]:
    """初始口径显式登记，保持原有种子值，不从显示名推断。"""
    by_code = {m["id"]: m for m in metrics}
    out = {}
    for codes in (("gmv", "gmv_tax"), ("repeat_sales", "repeat_sales_tax")):
        variants = [
            {
                "code": code,
                "label": label,
                "default": i == 0,
                "api": by_code[code]["api"],
                "version": by_code[code]["version"],
            }
            for i, (code, label) in enumerate(zip(codes, ("不含税", "含税"), strict=True))
            if code in by_code
        ]
        for code in codes:
            out[code] = variants
    return out


def _metric(r: Row, ctx: SeedContext) -> Row:
    ids = [x["id"] for x in ctx.load("METRIC_LIST.json")]
    stat_ids = [x["id"] for x in ctx.load("METRICS.json")]
    s = _index(ctx, "METRICS.json").get(r["id"], {})
    review = r.get("review") or {}
    return {
        "code": r["id"],
        "api_name": r["api"],
        "display_name": r["name"],
        "metric_group": r["group"],
        "unit": r["unit"],
        "version": r["version"],
        "status": from_api(MetricStatus, r["status"]),
        "owner": r["owner"],
        "review_cr": review.get("cr"),
        "review_note": review.get("note"),
        "formula": s.get("formula"),
        "dims": s.get("dims"),
        "value": s.get("value"),
        "prev": s.get("prev"),
        "stat_updated_at": _dt(s.get("updatedAt")),
        "def_rows": ctx.load("METRIC_DEF.json"),
        "dim_rows": ctx.load("METRIC_DIMS.json"),
        "version_rows": ctx.load("METRIC_VERSIONS.json"),
        "ref_rows": ctx.load("METRIC_REFS.json"),
        "disallowed_rows": METRIC_DENY,
        "seq": ids.index(r["id"]),
        "stat_seq": stat_ids.index(r["id"]) if r["id"] in stat_ids else None,
        "variants": seed_metric_variants(ctx.load("METRIC_LIST.json")).get(r["id"], []),
    }


# ---------------------------------------------------------------- 维度与码表


def _dimension(r: Row, ctx: SeedContext) -> Row:
    ids = [x["id"] for x in ctx.load("DIM_ITEMS.json")]
    return {
        "code": r["id"],
        "api_name": r["api"],
        "display_name": r["name"],
        "kind": from_api(DimKind, r["kind"]),
        "dim_group": r["group"],
        "status": from_api(DefStatus, r["status"]),
        "owner": r["owner"],
        "object_id": r["object"],
        "object_name": r["objectName"],
        "attr": r["attr"],
        "source_column": r["column"],
        "value_src": r["valueSrc"],
        "sync": r["sync"],
        "description": r["desc"],
        "qa": r["qa"],
        "levels_before": r.get("levelsBefore"),
        "metric_dims": r["metricDims"],
        "metric_note": r.get("metricNote"),
        "usage": r["usage"],
        "history": r["history"],
        "since_release": r["since"],
        "count_label": r.get("countLabel"),
        "values_note": r.get("valuesNote"),
        "draft_note": r.get("draftNote"),
        "seq": ids.index(r["id"]),
    }


def _dim_levels(r: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {
            "dimension_id": r["id"],
            "level_no": i,
            "name": lv["name"],
            "api_name": lv["api"],
            "source": lv["source"],
            "value_count": lv["count"],
            "note": lv.get("note"),
        }
        for i, lv in enumerate(r["levels"])
    ]


def _dim_values(r: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {
            "dimension_id": r["id"],
            "value_key": v["key"],
            "code": v["code"],
            "name": v["name"],
            "level_name": v["level"],
            "parent": v.get("parent"),
            "status": from_api(DimValueStatus, v["status"]),
            "synonyms": v["synonyms"],
            "record_count": v.get("count"),
            "src": v.get("src"),
            "note": v.get("note"),
            "ref": v.get("ref"),
            "since_release": v.get("since"),
            "until_release": v.get("until"),
            "draft_cr": v.get("draft"),
            "diff": v.get("diff"),
            "pub": v.get("pub"),
            "seq": i,
        }
        for i, v in enumerate(r["values"])
    ]


def _dim_mappings(r: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {
            "dimension_id": r["id"],
            "map_key": m["key"],
            "phrase": m["phrase"],
            "targets": m["targets"],
            "match": from_api(MatchMode, m["match"]),
            "source": from_api(MappingSource, m["source"]),
            "hits": m["hits"],
            "conflict": m.get("conflict"),
            "resolve": m.get("resolve"),
            "draft_cr": m.get("draft"),
            "diff": m.get("diff"),
            "seq": i,
        }
        for i, m in enumerate(r["mappings"])
    ]


# ---------------------------------------------------------------- 业务知识


def _knowledge(r: Row, ctx: SeedContext) -> Row:
    ids = [x["id"] for x in ctx.load("KNOWLEDGE.json")]
    return {
        "code": r["id"],
        "name": r["name"],
        "type": from_api(KnowledgeType, r["type"]),
        "status": from_api(KnowledgeStatus, r["status"]),
        "definition": r["definition"],
        "calc": r["calc"],
        "synonyms": r["synonyms"],
        "period": r["period"],
        "owner": r["owner"],
        "maps": r["maps"],
        "filters": r["filters"],
        "tries": r["tries"],
        "periods": r.get("periods"),
        "refs": r["refs"],
        "history": r["history"],
        "draft": r.get("draft"),
        "since_release": r.get("since"),
        "stopped_release": r.get("stoppedIn"),
        "cr_code": r.get("cr"),
        "content_updated_at": _dt(r["updatedAt"]),
        "seq": ids.index(r["id"]),
    }


def _knowledge_applies(r: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {
            "knowledge_id": r["id"],
            "target_kind": from_api(ApplyKind, a["kind"]),
            "target_code": a["id"],
            "target_name": a["name"],
            "seq": i,
        }
        for i, a in enumerate(r["applies"])
    ]


# ---------------------------------------------------------------- 问数路径


def group_key(g: Row) -> str:
    return f"{g['from']}→{g['to']}"


def _qa_paths(g: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {
            "group_key": group_key(g),
            "from_object_id": g["from"],
            "to_object_id": g["to"],
            "seq": i,
            "is_default": p["isDefault"],
            "hint": p.get("hint"),
            "check_note": g["check"] if p["isDefault"] else None,
        }
        for i, p in enumerate(g["paths"])
    ]


def _qa_path_steps(g: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {"path_id": (group_key(g), i), "step_no": j, "link_type_id": link}
        for i, p in enumerate(g["paths"])
        for j, link in enumerate(p["links"])
    ]


def _qa_path_examples(g: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {
            "from_object_id": g["from"],
            "to_object_id": g["to"],
            "seq": i,
            "question": e["q"],
            "path_id": (group_key(g), e["path"]) if e["path"] >= 0 else None,
            "how": e["how"],
        }
        for i, e in enumerate(g["examples"])
    ]


# ---------------------------------------------------------------- 变更单


def _cr_details(ctx: SeedContext) -> dict[str, Row]:
    key = "extra:CR_DETAILS.json"
    if key not in ctx.cache:
        f = ctx.json_dir.parent.parent / "extra" / JSON_MODULE / "CR_DETAILS.json"
        ctx.cache[key] = {x["id"]: x for x in json.loads(f.read_text(encoding="utf-8"))}
    return ctx.cache[key]


def _change_request(r: Row, ctx: SeedContext) -> Row:
    d = {k: v for k, v in (_cr_details(ctx).get(r["id"]) or {}).items() if k != "id"}
    return {
        "code": r["id"],
        "title": r["title"],
        "reason": r.get("reason") or "",
        "owner": r["owner"],
        "reviewer": r["reviewer"],
        "status": from_api(CRStatus, r["status"]),
        "level": from_api(CRLevel, r["level"]),
        "changes": r.get("changes") or 0,
        "reject_reason": r.get("rejectReason"),
        "release_id": r.get("release"),
        "detail": d,
        "status_at": _dt(r["updatedAt"]),
    }


# mock/releases.ts DRAFT_TARGETS（导出脚本没导这个常量，照抄；改 mock 时同步）
DRAFT_TARGETS: dict[str, dict[str, list[str]]] = {
    "CR-0014": {"action": ["adjust_region_budget"]},
    "CR-0013": {"event": ["store_price_changed"]},
    "CR-0012": {"object": ["member"], "link": ["member_holds_cards"]},
    "CR-0011": {"metric": ["gross_margin"], "rule": ["R-007"], "knowledge": ["K-018", "K-024"]},
    "CR-0009": {"object": ["store"], "dimension": ["format"]},
}


def _cr_targets(r: Row, ctx: SeedContext) -> list[Row]:
    pairs = [(k, i) for k, ids in DRAFT_TARGETS.get(r["id"], {}).items() for i in ids]
    return [
        {"cr_id": r["id"], "target_kind": k, "target_id": i, "is_new": False, "seq": n}
        for n, (k, i) in enumerate(pairs)
    ]


_OBJ = Ref(ObjectType, "code", nullable=False)
_QA_PATH = Ref(QaPath, ("group_key", "seq"))

SEEDS = [
    SeedSpec(
        ObjectType, "Q_OBJECT_TYPES.json", key=("code",), transform=_object_type, description="对象类型"
    ),
    SeedSpec(
        Property,
        "Q_OBJECT_TYPES.json",
        key=("object_type_id", "api_name"),
        transform=_properties,
        refs={"object_type_id": _OBJ},
        description="属性（OBJECT_PROPS）",
    ),
    SeedSpec(
        ObjectView,
        "derived/viewsOf.json",
        key=("object_type_id", "code"),
        transform=_view,
        refs={"object_type_id": _OBJ},
        description="对象视图",
    ),
    SeedSpec(
        LinkType,
        "LINKS.json",
        key=("code",),
        transform=_link,
        refs={"from_object_id": Ref(ObjectType, "code"), "to_object_id": Ref(ObjectType, "code")},
        description="关系类型",
    ),
    SeedSpec(
        LinkQuality,
        "LINKS.json",
        key=("link_type_id",),
        transform=_link_quality,
        refs={"link_type_id": Ref(LinkType, "code", nullable=False)},
        description="关系数据质量",
    ),
    SeedSpec(Metric, "METRIC_LIST.json", key=("code",), transform=_metric, description="指标"),
    SeedSpec(
        Dimension,
        "DIM_ITEMS.json",
        key=("code",),
        transform=_dimension,
        refs={"object_id": _OBJ},
        description="维度与码表",
    ),
    SeedSpec(
        DimLevel,
        "DIM_ITEMS.json",
        key=("dimension_id", "level_no"),
        transform=_dim_levels,
        refs={"dimension_id": Ref(Dimension, "code", nullable=False)},
    ),
    SeedSpec(
        DimValue,
        "DIM_ITEMS.json",
        key=("dimension_id", "value_key"),
        transform=_dim_values,
        refs={"dimension_id": Ref(Dimension, "code", nullable=False)},
    ),
    SeedSpec(
        DimMapping,
        "DIM_ITEMS.json",
        key=("dimension_id", "map_key"),
        transform=_dim_mappings,
        refs={"dimension_id": Ref(Dimension, "code", nullable=False)},
    ),
    SeedSpec(Knowledge, "KNOWLEDGE.json", key=("code",), transform=_knowledge, description="业务知识"),
    SeedSpec(
        KnowledgeApply,
        "KNOWLEDGE.json",
        key=("knowledge_id", "target_kind", "target_code"),
        transform=_knowledge_applies,
        refs={"knowledge_id": Ref(Knowledge, "code", nullable=False)},
    ),
    SeedSpec(
        QaPath,
        "PATH_GROUPS.json",
        key=("from_object_id", "to_object_id", "seq"),
        transform=_qa_paths,
        refs={"from_object_id": _OBJ, "to_object_id": _OBJ},
        description="问数路径",
    ),
    SeedSpec(
        QaPathStep,
        "PATH_GROUPS.json",
        key=("path_id", "step_no"),
        transform=_qa_path_steps,
        refs={
            "path_id": Ref(QaPath, ("group_key", "seq"), nullable=False),
            "link_type_id": Ref(LinkType, "code", nullable=False),
        },
    ),
    SeedSpec(
        QaPathExample,
        "PATH_GROUPS.json",
        key=("from_object_id", "to_object_id", "seq"),
        transform=_qa_path_examples,
        refs={"from_object_id": _OBJ, "to_object_id": _OBJ, "path_id": _QA_PATH},
    ),
    SeedSpec(
        ChangeRequest,
        "CHANGE_REQUESTS.json",
        key=("code",),
        transform=_change_request,
        refs={"release_id": Ref(OntologyRelease, "ver")},
        description="变更单",
    ),
    SeedSpec(
        CrTarget,
        "CHANGE_REQUESTS.json",
        key=("cr_id", "target_kind", "target_id"),
        transform=_cr_targets,
        refs={"cr_id": Ref(ChangeRequest, "code", nullable=False)},
        description="变更单涉及的定义（DRAFT_TARGETS）",
    ),
]
