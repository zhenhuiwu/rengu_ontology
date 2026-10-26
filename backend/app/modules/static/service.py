"""静态本体只读取数：当前列表SQL分页，历史从对应快照节分页，不混入当前定义。

- 保留原字段形状；完整编辑目录/所选定义不被列表分页截断。
- refs/usage属于运行数据的接口显式标runtimeFromCurrent，按所选资源读取；历史定义不回退当前表。
- 鉴权：所有接口依赖 CurrentUserDep（router._svc），未知 / 停用的 X-Demo-As → 401；
  功能权限按 api-index 权限表 require_func(页面 key, "view")（router.py）。
- `?previewAs=`：定义类数据不按被预览用户裁剪，原样返回（已核对前端：ObjectTypes / LinkTypes / Metrics /
  Dimensions / Glossary 都不读预览状态，预览只做整页可见性 canPage + 只读，见 static-api.md）。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.context import ViewContext
from app.common.pagination import Page, PageParams
from app.core.errors import AppError, NotFound
from app.modules.static import history, shapes
from app.modules.static.repo import StaticRepo
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.service import VersioningService

Obj = dict[str, Any]

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


KIND_MSG = "kind 只支持 metric、view、cr"


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


def metric_base(m: Obj) -> list[str]:
    f = m.get("formula") or ""
    out: list[str] = []
    for name, oid in FORMULA_OBJ:
        if name in f:
            out.append(oid)
            f = f.replace(name, "")
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
    def __init__(self, session: AsyncSession, ctx: ViewContext, user=None):
        self.session = session
        self.ctx = ctx
        self.user = user
        self.repo = StaticRepo(session)
        self._sections = {}

    @property
    def ver(self) -> str | None:
        return self.ctx.ver

    async def _section(self, name: str) -> Obj | None:
        """?ver= 时取该版本快照的一节；版本不存在 404。"""
        if not self.ver:
            return None
        if name not in self._sections:
            from app.modules.versioning.models import OntologyRelease

            row = (
                await self.session.execute(
                    select(OntologyRelease.id, OntologyRelease.snapshot[name]).where(
                        OntologyRelease.ver == self.ver
                    )
                )
            ).first()
            if row is None:
                raise NotFound("版本不存在", details={"ver": self.ver})
            self._sections[name] = row[1]
        return self._sections[name]

    async def _check_ver(self) -> None:
        if self.ver:
            from app.modules.versioning.models import OntologyRelease

            if (
                await self.session.scalar(select(OntologyRelease.id).where(OntologyRelease.ver == self.ver))
                is None
            ):
                raise NotFound("版本不存在", details={"ver": self.ver})

    # ================================================================ 对象类型

    async def object_list(self, kw: str, p: PageParams, focus_id: str | None = None) -> Obj:
        from app.modules.static.read_pages import object_list

        return await object_list(self, kw, p, focus_id)

    async def object_brief(self) -> list[Obj]:
        if self.ver:
            return [
                {
                    "key": code,
                    **{
                        key: data[key]
                        for key in ("name", "api", "group", "source", "owner", "pk")
                        if key in data
                    },
                }
                for code, data in (await self._section("objectTypes") or {}).items()
            ]
        return [shapes.object_brief(o) for o in await self.repo.object_types(light=True)]

    async def object_detail(self, oid: str) -> Obj:
        if self.ver:
            from app.modules.static.read_pages import object_detail

            return await object_detail(self, oid)
        await self._check_ver()
        o = await self.repo.object_type(oid)
        d = shapes.object_link_obj(o)
        d.update(
            status=o.status.value,
            meta=shapes.object_meta(o),
            brief=shapes.object_brief(o),
            db=shapes.db_of(o.datasource_name),
        )
        d["props"] = [shapes.prop(p) for p in o.properties]
        return d

    async def object_properties(self, oid: str, kw: str, p: PageParams, cr_id: str | None = None) -> Obj:
        from app.modules.static.read_pages import object_properties

        return await object_properties(self, oid, kw, p, cr_id)

    async def object_links(self, oid: str) -> list[Obj]:
        from app.modules.static.read_pages import object_links

        return await object_links(self, oid)

    async def object_mappings(self, oid: str, p: PageParams, cr_id: str | None = None) -> Obj:
        from app.modules.static.read_pages import object_properties

        return await object_properties(self, oid, "", p, cr_id, mappings=True)

    async def object_usage(
        self,
        oid: str,
        kind: str,
        q: str,
        flt: str,
        p: PageParams,
        cr_id: str | None = None,
        affected_only: bool = False,
    ) -> Obj:
        from app.modules.static.object_usage_pages import usage

        if kind not in ("metric", "view", "cr"):
            raise AppError(
                "参数校验失败",
                code="VALIDATION_FAILED",
                status_code=422,
                details={"fields": [{"field": "kind", "message": KIND_MSG}]},
            )
        return await usage(self, oid, kind, q, flt, p, cr_id, affected_only)

    # ================================================================ 关系类型

    async def _links(self) -> list[Obj]:
        if self.ver:
            return list((await self._section("links") or {}).values())
        return [shapes.link(row) for row in await self.repo.links()]

    async def graph(self) -> Obj:
        """总览图谱（OntologyOverview.vue draw()；api-index §3.4 `/ontology/graph`）。

        nodes = LINK_OBJECTS 形状，取已发布的对象类型（mock LINK_OBJECTS 没有草稿对象类型 purchase）；
        草稿对象（如 CR-0012 的储值卡）只在变更单里、不是对象类型，不返回
        （同 src/api/static.ts loadObjects）[推断]。
        edges = 两端都在 nodes 里的关系 `{from, to, linkId, status}`（`?ver=` 按该版本快照的关系）；
        `status` 给前端区分实线 / 虚线 [推断]。节点坐标仍由前端写死。
        """
        await self._check_ver()
        if self.ver:
            sec = await self._section("objectTypes") or {}
            nodes = [
                {k: d[k] for k in ("id", "name", "api", "group", "table") if k in d}
                for d in sec.values()
                if d.get("status", "已发布") == "已发布"
            ]
        else:
            objs = [o for o in await self.repo.object_types(light=True) if o.status.value == "已发布"]
            nodes = [shapes.object_link_obj(o) for o in objs]
        ids = {n["id"] for n in nodes}
        edges = [
            {
                "from": x["from"],
                "to": x["to"],
                "linkId": x["id"],
                "name": x.get("name") or x["id"],
                "status": x.get("status"),
            }
            for x in await self._links()
            if x.get("from") in ids and x.get("to") in ids
        ]
        return {"nodes": nodes, "edges": edges}

    async def link_list(self, q: str, src: str, st: str, p: PageParams, focus_id: str | None) -> Obj:
        from app.modules.static.read_pages import link_list

        return await link_list(self, q, src, st, p, focus_id)

    async def _link(self, lid: str) -> Obj:
        if self.ver:
            data = (await self._section("links") or {}).get(lid)
            if data is None:
                raise NotFound("关系类型在该版本不存在")
            return {**data, "usage": []}
        return shapes.link(await self.repo.link(lid))

    async def link_detail(self, lid: str) -> Obj:
        return {k: v for k, v in (await self._link(lid)).items() if k != "usage"}

    async def link_quality(self, lid: str) -> Obj:
        from app.modules.static.models import LinkQuality, LinkType

        if self.ver and lid not in (await self._section("links") or {}):
            raise NotFound("关系类型在该版本不存在")
        quality = await self.session.scalar(
            select(LinkQuality)
            .join(LinkType, LinkType.id == LinkQuality.link_type_id)
            .where(LinkType.code == lid)
        )
        if quality is None:
            raise NotFound("该关系没有数据质量检查结果", details={"id": lid})
        out = shapes.quality(quality)
        if self.ver:
            out["runtimeFromCurrent"] = True
        return out

    async def link_usage(self, lid: str, p: PageParams) -> Obj:
        from app.modules.static.models import LinkType
        from app.modules.static.read_pages import json_field_page

        if self.ver and lid not in (await self._section("links") or {}):
            raise NotFound("关系类型在该版本不存在")
        out = await json_field_page(self, LinkType, LinkType.usage, lid, p, missing_ok=bool(self.ver))
        if self.ver:
            out["runtimeFromCurrent"] = True
        return out

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

    async def metric_list(self, kw: str, group: str, p: PageParams, focus_id: str | None = None) -> Obj:
        from app.modules.static.read_pages import metric_list

        return await metric_list(self, kw, group, p, focus_id)

    async def metric_detail(self, mid: str) -> Obj:
        if self.ver:
            data = (await self._section("metrics") or {}).get(mid)
            if data is None:
                raise NotFound("指标在该版本不存在")
            return {
                **{k: v for k, v in data.items() if k != "versionRows"},
                "def": [],
                "dims": [],
                "disallowed": [],
            }
        await self._check_ver()
        return shapes.metric_detail(await self.repo.metric(mid))

    async def metric_versions(self, mid: str, p: PageParams) -> Obj:
        from app.modules.static.metric_versions import format_versions, output_versions

        if self.ver:
            data = (await self._section("metrics") or {}).get(mid)
            if data is None:
                raise NotFound("指标在该版本不存在")
            rows = format_versions(data.get("versionRows", []), data.get("version", ""))
            return paginate(rows, p)

        await self._check_ver()
        return paginate(await output_versions(self.session, await self.repo.metric(mid)), p, key="v")

    async def metric_refs(self, mid: str, p: PageParams) -> Obj:
        from app.modules.static.models import Metric
        from app.modules.static.read_pages import json_field_page

        if self.ver and mid not in (await self._section("metrics") or {}):
            raise NotFound("指标在该版本不存在")
        out = await json_field_page(self, Metric, Metric.ref_rows, mid, p, missing_ok=bool(self.ver))
        if self.ver:
            out["runtimeFromCurrent"] = True
        return out

    # ================================================================ 维度与码表

    async def _dims_full(self) -> list[Obj]:
        values, maps = await self.repo.dim_children()
        return [shapes.dimension(d, values[d.id], maps[d.id]) for d in await self.repo.dimensions()]

    async def _dims(self) -> list[Obj]:
        if self.ver:
            return [{**data, "usage": []} for data in (await self._section("dimensions") or {}).values()]
        return await self._dims_full()

    async def _dim(self, did: str) -> tuple[Obj, list[Obj]]:
        from app.modules.static.read_pages import single_dim

        return await single_dim(self, did)

    def _value_rows(self, d: Obj) -> list[Obj]:
        """Dimensions.vue valueRows（已发布视图）：live、非草稿；历史版本去掉带 diff 的；合并 pub 原值。"""
        ver = self.ver
        return [
            {**v, **(v.get("pub") or {})}
            for v in d["values"]
            if history.live(v, ver) and not v.get("draft") and (not ver or not v.get("diff"))
        ]

    async def dim_list(self, q: str, kind: str, group: str, p: PageParams, focus_id: str | None) -> Obj:
        from app.modules.static.read_pages import dim_list

        return await dim_list(self, q, kind, group, p, focus_id)

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
        if not full and not self.ver:
            from app.modules.static.models import Dimension

            row = await self.session.scalar(self.repo.dimension_query().where(Dimension.code == did))
            if row is None:
                raise NotFound("维度不存在")
            return self._dim_head(shapes.dimension(row, [], []))
        d, _ = await self._dim(did)
        return dict(d) if full else self._dim_head(d)

    async def dim_values(
        self, did: str, level: str, q: str, status: str, p: PageParams, cr_id: str | None = None
    ) -> Obj:
        from app.modules.static.read_pages import dim_values

        return await dim_values(self, did, level, q, status, p, cr_id)

    async def dim_mappings(self, did: str, conflict: bool, p: PageParams, cr_id: str | None = None) -> Obj:
        from app.modules.static.mapping_pages import dim_mappings

        return await dim_mappings(self, did, conflict, p, cr_id)

    def _phrases(self, dims: list[Obj]) -> dict[str, list[Obj]]:
        m: dict[str, list[Obj]] = {}

        def add(p: str, h: Obj) -> None:
            a = m.setdefault(p, [])
            if not any(x["item"] == h["item"] and x["value"] == h["value"] for x in a):
                a.append(h)

        for d in dims:
            values = self._value_rows(d)
            enabled = {v["name"] for v in values if v["status"] == "启用"}
            for v in values:
                if v["status"] == "启用":
                    add(v["name"], {"item": d["id"], "itemName": d["name"], "value": v["name"]})
                    for s in v["synonyms"]:
                        add(s, {"item": d["id"], "itemName": d["name"], "value": v["name"]})
            for x in d["mappings"]:
                if not x.get("draft") and (not self.ver or not x.get("diff")):
                    for t in x["targets"]:
                        if t in enabled:
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
                        "hits": None,
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
        from app.modules.static.read_pages import dim_usage

        return await dim_usage(self, did, p)

    async def dim_history(self, did: str, p: PageParams) -> Obj:
        if not self.ver:
            from app.modules.static.models import Dimension
            from app.modules.static.read_pages import json_field_page

            return await json_field_page(self, Dimension, Dimension.history, did, p)
        d, _ = await self._dim(did)
        rows = d["history"]
        if self.ver:
            rows = [h for h in rows if h["ver"] != "—" and h["ver"] <= self.ver]
        return paginate(rows, p, key="ver")

    # ================================================================ 业务知识

    async def _terms(self) -> list[Obj]:
        if self.ver:
            return [{**data, "refs": []} for data in (await self._section("terms") or {}).values()]
        return [shapes.knowledge(row) for row in await self.repo.knowledge()]

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
        from app.modules.static.read_pages import knowledge_list

        return await knowledge_list(self, q, ktype, st, p, focus_id)

    async def _term(self, kid: str) -> Obj:
        from app.modules.static.read_pages import single_term

        return await single_term(self, kid)

    async def knowledge_detail(self, kid: str) -> Obj:
        e = await self._term(kid)
        return {**{k: v for k, v in e.items() if k != "refs"}, "shownStatus": self._shown(e)}

    async def knowledge_refs(self, kid: str, p: PageParams) -> Obj:
        from app.modules.static.read_pages import knowledge_refs

        return await knowledge_refs(self, kid, p)

    async def knowledge_history(self, kid: str, p: PageParams) -> Obj:
        from app.modules.static.read_pages import knowledge_history

        return await knowledge_history(self, kid, p)

    async def synonym_conflicts(self) -> Obj:
        return {"conflicts": synonym_conflicts(await self._terms()), "rule": {}}
