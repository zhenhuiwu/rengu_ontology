"""静态本体只读接口的取数：按前端页面的筛选 / 分页规则在内存里做（定义数据量小）。

- 列表 / 详情记录 = mock 记录（shapes.py），键名、可选键与 mock 一致。
- `?ver=`（static-api.md §2.7）：版本不存在 404；关系、维度、业务知识按该版本快照里的定义返回，
  运行数据（关系质量 / 使用情况、维度使用情况、知识引用）从当前表补（§2.8）；对象类型、属性、指标返回当前。
  快照里没有这一节（例如该版本早于静态登记 section）时按当前表 + §2.7 规则现算 [推断]。
- 鉴权：所有接口依赖 CurrentUserDep（router._svc），未知 / 停用的 X-Demo-As → 401；
  功能权限按 api-index 权限表 require_func(页面 key, "view")（router.py）。
- `?previewAs=`：定义类数据不按被预览用户裁剪，原样返回（已核对前端：ObjectTypes / LinkTypes / Metrics /
  Dimensions / Glossary 都不读预览状态，预览只做整页可见性 canPage + 只读，见 static-api.md）。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import ViewContext
from app.common.pagination import Page, PageParams
from app.core.errors import AppError, NotFound
from app.modules.static import history, shapes
from app.modules.static.repo import StaticRepo
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.service import VersioningService

Obj = dict[str, Any]

# mock/knowledge.ts CONFLICT_RULE（冲突词的默认处理）
CONFLICT_RULE = {
    "老客": "默认按「复购客户」理解；问题里出现“注册”“积分”“权益”时按「老会员」",
    "大促": "默认取离提问日期最近的一次大促（当前为双十一）",
    "到家": "先追问：线上渠道全部，还是只看配送到家",
    "毛利率": "口径说明与指标同名，以指标为准，回答里附口径说明",
    "销售额": "指向指标选择说明，默认不含税",
}
# objectGov.ts：指标维度 → 对象类型；公式里的对象名 → 对象类型（长名在前）
DIM_OBJ = {
    "门店": "store",
    "区域": "region",
    "大区": "region",
    "渠道": "order",
    "品类": "category",
    "仓库": "warehouse",
    "商品": "product",
    "会员": "member",
}
FORMULA_OBJ = [
    ("订单行", "order_line"),
    ("退款行", "refund_line"),
    ("退款单", "refund"),
    ("退款", "refund"),
    ("订单", "order"),
    ("会员", "member"),
    ("商品", "product"),
]


def in_order(sec: Obj, cur: list[Obj]) -> list[tuple[str, Obj]]:
    """快照节按当前表的排序输出（JSONB 不保留键序；mock 的 xxxAt 保持数组原序），快照独有的排在后面。"""
    rank = {x["id"]: n for n, x in enumerate(cur)}
    return sorted(sec.items(), key=lambda kv: (rank.get(kv[0], len(rank)), kv[0]))


KIND_MSG = "kind 只支持 metric、view（变更单历史随变更单模块提供）"


def paginate(items: list[Obj], p: PageParams, focus_id: str | None = None, key: str = "id") -> Obj:
    """前端 slice 分页（骨架 PageParams.clamp + Page.of，同 src/mock/paginate.ts:13）。

    focusId 在筛选结果里时先把页码换成它所在的页（api-index §1 focusId），再按 total 收拢：
    page ≤ 0 已由 PageParams 收成 1；超过最后一页 → 最后一页，响应 page 为收拢后的值；
    total=0 → 第 1 页、空列表。
    """
    if focus_id:
        i = next((n for n, x in enumerate(items) if x.get(key) == focus_id), -1)
        if i >= 0:
            p = PageParams(page=i // p.size + 1, size=p.size)
    c = p.clamp(len(items))
    return Page[Obj].of(items[c.offset : c.offset + c.size], len(items), c).model_dump()


def hits_for(p: str) -> int:
    """Dimensions.vue hitsFor：说法的近 7 天命中数（演示用的确定性哈希）。"""
    h = 0
    for c in p:
        # JS charCodeAt 按 UTF-16 码元；中文都在 BMP 内，与 ord 一致
        h = (h * 31 + ord(c)) % 997
    return 20 + h * 3


def metric_base(m: Obj) -> list[str]:
    f = m["formula"]
    out: list[str] = []
    for name, oid in FORMULA_OBJ:
        if name in f:
            out.append(oid)
            f = f.replace(name, "")
    if not out:
        out.append("order" if "订单数" in m["formula"] else "order_line")
    return list(dict.fromkeys(out))


def synonym_conflicts(entries: list[Obj]) -> list[Obj]:
    """mock/knowledge.ts synonymConflicts：同一说法指向多个条目（已停用的不算）。

    Map → [{word, ids}] [推断]。
    """
    m: dict[str, list[str]] = {}
    for e in entries:
        if e["status"] == "已停用":
            continue
        for w in [e["name"], *e["synonyms"]]:
            m.setdefault(w, []).append(e["id"])
    return [{"word": w, "ids": ids} for w, ids in m.items() if len(set(ids)) > 1]


class StaticService:
    def __init__(self, session: AsyncSession, ctx: ViewContext):
        self.session = session
        self.ctx = ctx
        self.repo = StaticRepo(session)

    @property
    def ver(self) -> str | None:
        return self.ctx.ver

    async def _section(self, name: str) -> Obj | None:
        """?ver= 时取该版本快照的一节；版本不存在 404。"""
        if not self.ver:
            return None
        await ReleaseRepo(self.session).by_ver_or_404(self.ver)
        snap = await VersioningService().get_snapshot(self.session, self.ver) or {}
        return snap.get(name)

    async def _check_ver(self) -> None:
        if self.ver:
            await ReleaseRepo(self.session).by_ver_or_404(self.ver)

    # ================================================================ 对象类型

    async def object_list(self, kw: str, p: PageParams) -> Obj:
        await self._check_ver()
        counts = await self.repo.prop_counts()
        items = [shapes.object_list_item(o, counts.get(o.id, 0)) for o in await self.repo.object_types()]
        if kw:
            items = [x for x in items if kw in x["name"] or kw.lower() in x["api"].lower()]
        return paginate(items, p)

    async def object_brief(self) -> list[Obj]:
        return [shapes.object_brief(o) for o in await self.repo.object_types()]

    async def object_detail(self, oid: str) -> Obj:
        await self._check_ver()
        o = await self.repo.object_type(oid)
        d = shapes.object_link_obj(o)
        d.update(
            status=o.status.value,
            meta=shapes.object_meta(o),
            brief=shapes.object_brief(o),
            props=[shapes.prop(p) for p in o.properties],
            db=shapes.db_of(o.datasource_name),
        )
        return d

    async def object_properties(self, oid: str, kw: str, p: PageParams) -> Obj:
        await self._check_ver()
        o = await self.repo.object_type(oid)
        items = [shapes.prop(p) for p in o.properties]
        if kw:
            items = [p for p in items if kw in p["name"] or kw in p["api"]]
        out = paginate(items, p)
        out["diff"] = []  # 草稿差异来自变更单（不在本次范围）[推断]
        return out

    async def object_links(self, oid: str) -> list[Obj]:
        """ObjectTypes.vue:154：当前对象作为源端的已发布关系（历史版本再按 since ≤ V）。"""
        await self.repo.object_type(oid)
        links = await self._links()
        return [x for x in links if x["from"] == oid and x["status"] == "已发布"]

    async def object_mappings(self, oid: str, p: PageParams) -> Obj:
        await self._check_ver()
        o = await self.repo.object_type(oid)
        return paginate([shapes.mapping(p) for p in o.properties], p)

    async def object_usage(self, oid: str, kind: str, q: str, flt: str, p: PageParams) -> Obj:
        await self._check_ver()
        o = await self.repo.object_type(oid)
        if kind == "view":
            items = [shapes.view(v, o.code) for v in await self.repo.views(o.id)]
            items = [
                v
                for v in items
                if (not flt or flt == "全部" or v["kind"] == flt)
                and (not q or q in v["name"] or q in v["owner"])
            ]
            return paginate(items, p)
        if kind == "metric":
            items = []
            for m in await self.repo.stat_metrics():
                d = shapes.metric_stat(m)
                base = metric_base(d)
                via = [x for x in d["dims"] if DIM_OBJ.get(x) == oid]
                if oid in base or via:
                    items.append({**d, "base": base, "via": via})
            if flt == "base":
                items = [u for u in items if oid in u["base"]]
            elif flt == "dim":
                items = [u for u in items if u["via"] and oid not in u["base"]]
            return paginate(items, p)
        # 统一参数校验错误（core/errors.py:6）：422 VALIDATION_FAILED，details.fields = [{field, message}]。
        # TODO(变更单模块)：kind=cr 等变更单模块接入后返回该对象的变更单历史（static-api.md）。
        raise AppError(
            "参数校验失败",
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": "kind", "message": KIND_MSG}]},
        )

    # ================================================================ 关系类型

    async def _links(self) -> list[Obj]:
        rows = await self.repo.links()
        cur = [shapes.link(x) for x in rows]
        if not self.ver:
            return cur
        sec = await self._section("links")
        if sec is None:
            return history.links_at(cur, self.ver)
        runtime = {x["id"]: {k: x[k] for k in history.LINK_RUNTIME if k in x} for x in cur}
        return [{**d, **runtime.get(i, {})} for i, d in in_order(sec, cur)]

    async def graph(self) -> Obj:
        """总览图谱（OntologyOverview.vue draw()；api-index §3.4 `/ontology/graph`）。

        nodes = LINK_OBJECTS 形状，取已发布的对象类型（mock LINK_OBJECTS 没有草稿对象类型 purchase）；
        草稿对象（如 CR-0012 的储值卡）只在变更单里、不是对象类型，不返回
        （同 src/api/static.ts loadObjects）[推断]。
        edges = 两端都在 nodes 里的关系 `{from, to, linkId, status}`（`?ver=` 按该版本快照的关系）；
        `status` 给前端区分实线 / 虚线 [推断]。节点坐标仍由前端写死。
        """
        await self._check_ver()
        objs = [o for o in await self.repo.object_types() if o.status.value == "已发布"]
        nodes = [shapes.object_link_obj(o) for o in objs]
        ids = {n["id"] for n in nodes}
        edges = [
            {"from": x["from"], "to": x["to"], "linkId": x["id"], "status": x.get("status")}
            for x in await self._links()
            if x.get("from") in ids and x.get("to") in ids
        ]
        return {"nodes": nodes, "edges": edges}

    async def link_list(self, q: str, src: str, st: str, p: PageParams, focus_id: str | None) -> Obj:
        objs = {o.code: o.display_name for o in await self.repo.object_types()}
        name = lambda i: objs.get(i, i)  # noqa: E731  mock objName：不在 LINK_OBJECTS 的原样显示 id
        items = [
            x
            for x in await self._links()
            if (not src or x["from"] == src)
            and (not st or x["status"] == st)
            and (
                not q
                or q in x["name"]
                or q in x.get("api", x["id"])
                or q in name(x["from"])
                or q in name(x["to"])
            )
        ]
        return paginate(items, p, focus_id)

    async def _link(self, lid: str) -> Obj:
        for x in await self._links():
            if x["id"] == lid:
                return x
        raise NotFound("关系类型不存在", details={"id": lid, "ver": self.ver})

    async def link_detail(self, lid: str) -> Obj:
        return {k: v for k, v in (await self._link(lid)).items() if k != "usage"}

    async def link_quality(self, lid: str) -> Obj:
        q = (await self._link(lid)).get("quality")
        if q is None:
            raise NotFound("该关系没有数据质量检查结果", details={"id": lid})
        return q

    async def link_usage(self, lid: str, p: PageParams) -> Obj:
        return paginate((await self._link(lid))["usage"], p)

    async def link_paths(self, frm: str | None, to: str | None) -> list[Obj]:
        """PATH_GROUPS：取该版本（默认当前版本）快照的 qaPaths 节；
        该节发布时由 onto_qa_path* 表组装（sections.py QaPathsSection）。
        路径只保留其关系在该版本都可见的（LinkTypes.vue groupsOf 的可见性过滤；「≥2 条」由页面自己判断）。"""
        if self.ver:
            await ReleaseRepo(self.session).by_ver_or_404(self.ver)
        snap = await VersioningService().get_snapshot(self.session, self.ver) or {}
        visible = {x["id"] for x in await self._links()}
        out = []
        for g in (snap.get("qaPaths") or {}).values():
            if (frm and g["from"] != frm) or (to and g["to"] != to):
                continue
            paths = [x for x in g["paths"] if all(i in visible for i in x["links"])]
            if paths:
                out.append({**g, "paths": paths})
        return out

    # ================================================================ 指标

    async def metric_list(self, kw: str, group: str, p: PageParams) -> Obj:
        await self._check_ver()
        items = [shapes.metric_list_item(m) for m in await self.repo.metrics()]
        items = [
            m
            for m in items
            if (not group or group == "全部" or m["group"] == group)
            and (not kw or kw in m["name"] or kw in m["api"])
        ]
        return paginate(items, p)

    async def metric_detail(self, mid: str) -> Obj:
        await self._check_ver()
        return shapes.metric_detail(await self.repo.metric(mid))

    async def metric_versions(self, mid: str, p: PageParams) -> Obj:
        await self._check_ver()
        return paginate(list((await self.repo.metric(mid)).version_rows), p, key="v")

    async def metric_refs(self, mid: str, p: PageParams) -> Obj:
        await self._check_ver()
        return paginate(list((await self.repo.metric(mid)).ref_rows), p, key="name")

    # ================================================================ 维度与码表

    async def _dims_full(self) -> list[Obj]:
        values, maps = await self.repo.dim_children()
        return [shapes.dimension(d, values[d.id], maps[d.id]) for d in await self.repo.dimensions()]

    async def _dims(self) -> list[Obj]:
        """当前：完整记录（values / mappings 含草稿行，同 mock）。

        历史版本：快照里该版本的定义 + 当前 usage。
        """
        cur = await self._dims_full()
        if not self.ver:
            return cur
        sec = await self._section("dimensions")
        if sec is None:
            return history.dimensions_at(cur, self.ver)
        usage = {x["id"]: x["usage"] for x in cur}
        return [{**d, "usage": usage.get(i, [])} for i, d in in_order(sec, cur)]

    async def _dim(self, did: str) -> tuple[Obj, list[Obj]]:
        dims = await self._dims()
        for d in dims:
            if d["id"] == did:
                return d, dims
        raise NotFound("维度不存在", details={"id": did, "ver": self.ver})

    def _value_rows(self, d: Obj) -> list[Obj]:
        """Dimensions.vue valueRows（已发布视图）：live、非草稿；历史版本去掉带 diff 的；合并 pub 原值。"""
        ver = self.ver
        return [
            {**v, **(v.get("pub") or {})}
            for v in d["values"]
            if history.live(v, ver) and not v.get("draft") and (not ver or not v.get("diff"))
        ]

    async def dim_list(self, q: str, kind: str, group: str, p: PageParams, focus_id: str | None) -> Obj:
        items = [
            d
            for d in await self._dims()
            if (not kind or kind == "all" or d["kind"] == kind)
            and (not group or d["group"] == group)
            and (
                not q
                or q in d["name"]
                or q in d["api"]
                or q in d["objectName"]
                or any(q in v["name"] or q in v["synonyms"] for v in d["values"])
            )
        ]
        out = paginate(items, p, focus_id)
        out["items"] = [self._dim_summary(d) for d in out["items"]]
        out["groups"] = list(dict.fromkeys(d["group"] for d in await self._dims()))
        return out

    def _dim_summary(self, d: Obj) -> Obj:
        """列表项：DimItem 去掉 values / mappings / usage / history，附已发布取值数 valueCount [推断]。"""
        out = self._dim_head(d)
        out["valueCount"] = len([v for v in d["values"] if history.live(v, self.ver) and not v.get("draft")])
        return out

    @staticmethod
    def _dim_head(d: Obj) -> Obj:
        """历史版本的层级 / 说明已在 _dims 里按 levelsBefore 换好（history.dimension_at 或快照）。"""
        return {k: v for k, v in d.items() if k not in ("values", "mappings", "usage", "history")}

    async def dim_detail(self, did: str, full: bool = False) -> Obj:
        d, _ = await self._dim(did)
        return dict(d) if full else self._dim_head(d)

    async def dim_values(self, did: str, level: str, q: str, status: str, p: PageParams) -> Obj:
        d, _ = await self._dim(did)
        rows = self._value_rows(d)
        off = len([v for v in rows if v["status"] == "停用"])
        # Dimensions.vue valueSummary / valueLevels（已发布视图没有草稿改动）
        out_summary = f"共 {len(rows)} 个取值" + (f"，停用 {off} 个" if off else "")
        levels = list(dict.fromkeys(v["level"] for v in rows))
        rows = [
            v
            for v in rows
            if (not level or v["level"] == level)
            and (not status or v["status"] == status)
            and (not q or q in v["name"] or q in v["code"] or any(q in s for s in v["synonyms"]))
        ]
        out = paginate(rows, p, key="key")
        out["summary"] = out_summary  # [推断] 取值页签上方的统计与层级选项
        out["levels"] = levels
        return out

    async def dim_mappings(self, did: str, conflict: bool, p: PageParams) -> Obj:
        d, dims = await self._dim(did)
        rows = self._map_rows(d, dims)
        out = paginate([r for r in rows if r["conflictText"]] if conflict else rows, p, key="key")
        out["conflictCount"] = len([r for r in rows if r["conflictText"]])
        return out

    def _phrases(self, dims: list[Obj]) -> dict[str, list[Obj]]:
        m: dict[str, list[Obj]] = {}

        def add(p: str, h: Obj) -> None:
            a = m.setdefault(p, [])
            if not any(x["item"] == h["item"] and x["value"] == h["value"] for x in a):
                a.append(h)

        for d in dims:
            for v in d["values"]:
                if history.live(v, self.ver) and v["status"] == "启用" and not v.get("draft"):
                    add(v["name"], {"item": d["id"], "itemName": d["name"], "value": v["name"]})
                    for s in v["synonyms"]:
                        add(s, {"item": d["id"], "itemName": d["name"], "value": v["name"]})
            for x in d["mappings"]:
                if not x.get("draft"):
                    for t in x["targets"]:
                        add(x["phrase"], {"item": d["id"], "itemName": d["name"], "value": t})
        return m

    def _map_rows(self, d: Obj, dims: list[Obj]) -> list[Obj]:
        """Dimensions.vue mapRows + conflictText：显式映射 + 由启用取值自动生成的说法。"""
        phrases = self._phrases(dims)
        hits_of = lambda p: phrases.get(p, [])  # noqa: E731
        explicit = [m for m in d["mappings"] if not m.get("draft") and (not self.ver or not m.get("diff"))]
        seen = {m["phrase"] for m in explicit}
        rows: list[Obj] = [dict(m) for m in explicit]
        for v in self._value_rows(d):
            if v["status"] != "启用":
                continue

            def add(p: str, src: str, v: Obj = v) -> None:
                if p in seen:
                    return
                others = [h for h in hits_of(p) if not (h["item"] == d["id"] and h["value"] == v["name"])]
                if src == "取值名称" and not others:
                    return
                seen.add(p)
                rows.append(
                    {
                        "key": f"{p}>{v['name']}",
                        "phrase": p,
                        "targets": [v["name"]],
                        "match": "精确",
                        "source": src,
                        "hits": hits_for(p),
                        "auto": "、".join(f"{h['itemName']} · {h['value']}" for h in others),
                    }
                )

            add(v["name"], "取值名称")
            for s in v["synonyms"]:
                add(s, "取值同义词")
        out = []
        for r in rows:
            if "auto" not in r and not r.get("conflict"):
                others = [
                    h
                    for h in hits_of(r["phrase"])
                    if not (h["item"] == d["id"] and h["value"] in r["targets"])
                ]
                if others:
                    r["auto"] = "、".join(f"{h['itemName']} · {h['value']}" for h in others)
            r["conflictText"] = r.get("conflict") or (f"与 {r['auto']} 同名" if r.get("auto") else "")
            out.append(r)
        return out

    async def dim_usage(self, did: str, p: PageParams) -> Obj:
        """Dimensions.vue usageRows = metricUsage(d)（METRICS 里有共同维度的指标）+ d.usage。"""
        d, _ = await self._dim(did)
        rows = []
        for m in map(shapes.metric_stat, await self.repo.stat_metrics()):
            common = [x for x in m["dims"] if x in d["metricDims"]]
            if not common:
                continue
            note = (d.get("metricNote") or {}).get(m["id"])
            if note is None:
                note = (
                    "允许按“"
                    + "”“".join(common)
                    + "”拆分"
                    + ("（指标草稿）" if m["status"] == "草稿" else "")
                )
            rows.append(
                {"type": "指标", "name": m["name"], "hover": {"kind": "metric", "id": m["id"]}, "note": note}
            )
        return paginate(rows + list(d["usage"]), p, key="name")

    async def dim_history(self, did: str, p: PageParams) -> Obj:
        d, _ = await self._dim(did)
        rows = d["history"]
        if self.ver:
            rows = [h for h in rows if h["ver"] != "—" and h["ver"] <= self.ver]
        return paginate(rows, p, key="ver")

    # ================================================================ 业务知识

    async def _terms(self) -> list[Obj]:
        rows = await self.repo.knowledge()
        cur = [shapes.knowledge(k) for k in rows]
        if not self.ver:
            return cur
        sec = await self._section("terms")
        if sec is None:
            return history.terms_at(cur, self.ver)
        refs = {x["id"]: x["refs"] for x in cur}
        return [{**e, "refs": refs.get(i, [])} for i, e in in_order(sec, cur)]

    def _shown(self, e: Obj) -> str:
        """Glossary.vue shownStatus。[推断] 变更单表不在本次范围：所在草稿单 = 状态为草稿的条目自带 cr，
        或历史里「草稿」行的 cr；有草稿单一律显示「草稿中」（变更单审核中显示「审核中」待变更单模块接入）。"""
        s = e["status"]
        if self.ver:
            return "已停用" if s == "已停用" else "已发布"
        if s == "已停用":
            return "已停用"
        cr = next((h.get("cr") for h in e["history"] if h["status"] == "草稿" and h.get("cr")), None)
        if s == "草稿":
            cr = cr or e.get("cr")
        if not cr:
            return "草稿中" if s == "草稿" else "已发布"
        return "草稿中"

    async def knowledge_list(self, q: str, ktype: str, st: str, p: PageParams, focus_id: str | None) -> Obj:
        items = []
        for e in await self._terms():
            shown = self._shown(e)
            if (
                (not ktype or e["type"] == ktype)
                and (not st or shown == st)
                and (not q or q in e["name"] or q in e["id"] or any(q in s for s in e["synonyms"]))
            ):
                items.append({**e, "shownStatus": shown})
        out = paginate(items, p, focus_id)
        out["items"] = [{k: v for k, v in x.items() if k != "refs"} for x in out["items"]]
        return out

    async def _term(self, kid: str) -> Obj:
        for e in await self._terms():
            if e["id"] == kid:
                return e
        raise NotFound("业务知识不存在", details={"id": kid, "ver": self.ver})

    async def knowledge_detail(self, kid: str) -> Obj:
        e = await self._term(kid)
        return {**{k: v for k, v in e.items() if k != "refs"}, "shownStatus": self._shown(e)}

    async def knowledge_refs(self, kid: str, p: PageParams) -> Obj:
        return paginate((await self._term(kid))["refs"], p, key="name")

    async def synonym_conflicts(self) -> Obj:
        return {"conflicts": synonym_conflicts(await self._terms()), "rule": CONFLICT_RULE}
