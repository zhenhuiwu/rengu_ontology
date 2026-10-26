"""静态负责的 7 个快照 section（app/contracts/versioning.py 第 5、10 条；static-api.md §2.7、§2.8）。

- section 内按前端 id 作键；只放定义，运行数据（关系质量 / 使用情况、维度使用情况、知识引用、
  对象记录数 / 截止时间 / 问数统计、指标当前值）不进快照，读历史版本时从当前表补。
- seed_section(ver, ctx)：读导出 JSON，按 §2.7 的版本规则裁出该版本可见的内容。
- build_section(session, base, crs)：发布时组装。变更单展开不在本次范围 [推断]：
  base 不为空就沿用上一版，否则从当前定义表组装「当前已发布」那一节。
- restore_section（整版回滚写表）：只有 terms 实现（writeback.restore_terms），其余节只回快照。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.static import history, shapes
from app.modules.static.repo import StaticRepo
from app.modules.static.writeback import DIRTY, restore_terms

Obj = dict[str, Any]
G = "static"  # 导出 JSON 所在组；seed ctx 是 versioning 组的，读静态文件要带 group


def object_def_from_json(brief: Obj, meta: Obj, table: str | None, status: str) -> Obj:
    d = {
        "id": brief["key"],
        "name": brief["name"],
        "api": brief["api"],
        "group": brief["group"],
        "domain": meta["domain"],
        "desc": meta["desc"],
        "conflict": meta["conflict"],
        "lifecycle": meta["lifecycle"],
        "sync": meta["sync"],
        "qs": meta["qs"],
        "status": status,
    }
    return shapes.opt(
        d, source=brief.get("source"), owner=brief.get("owner"), pk=brief.get("pk"), table=table
    )


def object_def(o: Any) -> Obj:
    meta = shapes.object_meta(o)
    return object_def_from_json(shapes.object_brief(o), meta, o.source_table, o.status.value)


def metric_def_from_json(row: Obj, stat: Obj | None) -> Obj:
    d = dict(row)
    if stat:
        d.update(formula=stat["formula"], dimNames=stat["dims"])
    return d


class _Base:
    section = ""

    async def build_section(self, session: AsyncSession, base: Any, change_order_ids: list[str]) -> Any:
        # 发布写回改到本节（writeback.DIRTY）就按定义表重组，否则复用上一版
        if base is not None and self.section not in session.info.get(DIRTY, ()):
            return base
        return await self.current(StaticRepo(session))

    async def current(self, repo: StaticRepo) -> Any:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def seed_section(self, ver: str, ctx: Any) -> Any:  # pragma: no cover - 子类实现
        raise NotImplementedError


class ObjectTypesSection(_Base):
    """objectTypes：{对象 id: 定义}。对象类型没有 since，读历史版本返回当前（§2.7）。"""

    section = "objectTypes"

    async def current(self, repo: StaticRepo) -> Obj:
        return {o.code: object_def(o) for o in await repo.object_types()}

    def seed_section(self, ver: str, ctx: Any) -> Obj:
        from app.modules.static.seeds import OBJECT_STATUS

        meta = {c["args"][0]: c["result"] for c in ctx.load("derived/objectMeta.json", group=G)}
        tables = {x["id"]: x.get("table") for x in ctx.load("LINK_OBJECTS.json", group=G)}
        out = {}
        for b in ctx.load("Q_OBJECT_TYPES.json", group=G):
            st = OBJECT_STATUS.get(b["key"])
            out[b["key"]] = object_def_from_json(
                b, meta[b["key"]], tables.get(b["key"]), st.value if st else "已发布"
            )
        return out


class PropertiesSection(_Base):
    """properties：{对象 id: {属性 API 名: 属性}}。"""

    section = "properties"

    async def current(self, repo: StaticRepo) -> Obj:
        return {o.code: {p.api_name: shapes.prop(p) for p in o.properties} for o in await repo.object_types()}

    def seed_section(self, ver: str, ctx: Any) -> Obj:
        props = ctx.load("OBJECT_PROPS.json", group=G)
        return {k: {p["api"]: p for p in v} for k, v in props.items()}


class LinksSection(_Base):
    """links：{关系 id: 定义}，已发布且 since ≤ V。"""

    section = "links"

    async def current(self, repo: StaticRepo) -> Obj:
        items = [shapes.link(x, with_runtime=False) for x in await repo.links()]
        return {x["id"]: x for x in history.links_at(items, None)}

    def seed_section(self, ver: str, ctx: Any) -> Obj:
        items = [history.strip(x, history.LINK_RUNTIME) for x in ctx.load("LINKS.json", group=G)]
        return {x["id"]: x for x in history.links_at(items, ver)}


class MetricsSection(_Base):
    """metrics：{指标 id: 定义}（METRIC_LIST 项 + formula / dimNames），不含当前值。"""

    section = "metrics"

    async def current(self, repo: StaticRepo) -> Obj:
        out = {}
        for m in await repo.metrics():
            stat = shapes.metric_stat(m) if m.formula is not None else None
            out[m.code] = metric_def_from_json(shapes.metric_list_item(m), stat)
        return out

    def seed_section(self, ver: str, ctx: Any) -> Obj:
        stats = {x["id"]: x for x in ctx.load("METRICS.json", group=G)}
        return {
            x["id"]: metric_def_from_json(x, stats.get(x["id"]))
            for x in ctx.load("METRIC_LIST.json", group=G)
        }


class DimensionsSection(_Base):
    """dimensions：{维度 id: 该版本的定义}。

    since ≤ V；取值 since ≤ V < until；V 早于 levelsBefore.ver 用旧层级。
    """

    section = "dimensions"

    async def current(self, repo: StaticRepo) -> Obj:
        values, maps = await repo.dim_children()
        items = [
            shapes.dimension(d, values[d.id], maps[d.id], with_runtime=False) for d in await repo.dimensions()
        ]
        return {x["id"]: x for x in history.dimensions_at(items, None)}

    def seed_section(self, ver: str, ctx: Any) -> Obj:
        items = [history.strip(x, history.DIM_RUNTIME) for x in ctx.load("DIM_ITEMS.json", group=G)]
        return {x["id"]: x for x in history.dimensions_at(items, ver)}


class TermsSection(_Base):
    """terms：{知识 id: 该版本的条目}（since ≤ V；停用按 stoppedIn；历史只留 ≤ V）。"""

    section = "terms"

    async def current(self, repo: StaticRepo) -> Obj:
        items = [shapes.knowledge(k, with_runtime=False) for k in await repo.knowledge()]
        return {x["id"]: x for x in history.terms_at(items, None)}

    def seed_section(self, ver: str, ctx: Any) -> Obj:
        items = [history.strip(x, history.TERM_RUNTIME) for x in ctx.load("KNOWLEDGE.json", group=G)]
        return {x["id"]: x for x in history.terms_at(items, ver)}

    async def restore_section(self, session: AsyncSession, content: Any, *, release_id: int) -> None:
        """整版回滚时把 knowledge 表恢复成 content（writeback.restore_terms）。其余节只回快照。"""
        await restore_terms(session, content or {}, release_id=release_id)


class QaPathsSection(_Base):
    """qaPaths：{"from→to": 路径组}，路径组 = PATH_GROUPS 项原样
    {from, to, check, paths:[{links, isDefault, hint?}], examples:[{q, path, how}]}。

    当前定义从 onto_qa_path / onto_qa_path_step / onto_qa_path_example 组装
    （examples.path 是 paths 下标，-1 = 先追问）。
    """

    section = "qaPaths"

    async def current(self, repo: StaticRepo) -> Obj:
        # 按 repo id 顺序（组首次出现顺序，见 repo.qa_paths）；不要按 object_id 重排。
        paths, examples = await repo.qa_paths()
        out: Obj = {}

        def group(frm: str, to: str) -> Obj:
            key = f"{frm}→{to}"
            return out.setdefault(key, {"from": frm, "to": to, "check": None, "paths": [], "examples": []})

        for p in paths:
            g = group(p.from_object.code, p.to_object.code)
            if p.is_default:
                g["check"] = p.check_note
            g["paths"].append(
                shapes.opt({"links": [s.link.code for s in p.steps], "isDefault": p.is_default}, hint=p.hint)
            )
        for e in examples:
            # 组只有 examples、没有 paths 时也建空组（避免 KeyError）
            g = group(e.from_object.code, e.to_object.code)
            g["examples"].append({"q": e.question, "path": e.path.seq if e.path else -1, "how": e.how})
        return out

    def seed_section(self, ver: str, ctx: Any) -> Obj:
        return {f"{g['from']}→{g['to']}": g for g in ctx.load("PATH_GROUPS.json", group=G)}


BUILDERS = [
    ObjectTypesSection(),
    PropertiesSection(),
    LinksSection(),
    MetricsSection(),
    DimensionsSection(),
    TermsSection(),
    QaPathsSection(),
]
