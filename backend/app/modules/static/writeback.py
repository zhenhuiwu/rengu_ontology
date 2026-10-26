"""发布写回：整版发布时（同一事务、create_version 之前）把已通过变更单的 cr_item 写回定义表。

按 (target_kind, section) 登记写回函数（register）；没登记的段跳过、原样留在 cr_item 里，
发布事件 desc 里列出「未写回」。静态登记：knowledge/content、knowledge/status、object/properties。
动作 / 事件（Palantir）、规则 / 生命周期 lc_*（动态）由各模块自己 register，静态不碰它们的表。

写回后把改到的快照节名记进 session.info[DIRTY]，sections._Base.build_section 据此重组该节，
其它节照旧复用上一版快照。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.schema import _fmt_dt
from app.modules.static.models import (
    ApplyKind,
    Cardinality,
    ChangeRequest,
    ConflictStrategy,
    CrItem,
    CrTarget,
    DefStatus,
    Dimension,
    DimMapping,
    DimValue,
    DimValueStatus,
    Knowledge,
    KnowledgeApply,
    KnowledgeStatus,
    KnowledgeType,
    Lifecycle,
    LinkType,
    MappingSource,
    MatchMode,
    Metric,
    MetricStatus,
    ObjectStatus,
    ObjectType,
    Property,
)

log = logging.getLogger(__name__)

DIRTY = "static_dirty_sections"


@dataclass
class Ctx:
    session: AsyncSession
    cr: ChangeRequest
    ver: str
    now: datetime
    by: str
    is_new: bool
    name: str | None = None  # cr_target.target_name（新建定义的显示名兜底）


Hook = Callable[[Ctx, str, list[CrItem]], Awaitable[set[str]]]
"""(ctx, target_id, 同一目标同一段的 cr_item) -> 改到的快照节名"""

HOOKS: dict[tuple[str, str], Hook] = {}


def register(kind: str, section: str) -> Callable[[Hook], Hook]:
    def deco(fn: Hook) -> Hook:
        HOOKS[(kind, section)] = fn
        return fn

    return deco


def _bad(msg: str, **details: Any) -> AppError:
    return AppError(msg, code="WRITEBACK_FAILED", status_code=409, details=details)


def _label(enum: Any, v: Any, field: str) -> Any:
    try:
        return enum(v)
    except ValueError:
        raise _bad(f"「{field}」的取值“{v}”不认识", field=field, value=v) from None


async def apply_crs(
    session: AsyncSession, crs: list[ChangeRequest], ver: str, now: datetime, by: str
) -> list[str]:
    """依次写回各单（按单号升序）。返回没登记写回的「kind/section」（去重），写进发布事件。"""
    dirty: set[str] = session.info.setdefault(DIRTY, set())
    skipped: list[str] = []
    for cr in sorted(crs, key=lambda c: c.code):
        new = {
            (t.target_kind, t.target_id): (t.is_new, t.target_name)
            for t in await session.scalars(select(CrTarget).where(CrTarget.cr_id == cr.id))
        }
        items = (
            await session.scalars(select(CrItem).where(CrItem.cr_id == cr.id).order_by(CrItem.seq, CrItem.id))
        ).all()
        groups: dict[tuple[str, str, str], list[CrItem]] = {}
        for i in items:
            groups.setdefault((i.target_kind, i.target_id, i.section), []).append(i)
        for (kind, tid, sec), rows in groups.items():
            hook = HOOKS.get((kind, sec))
            if hook is None:
                if f"{kind}/{sec}" not in skipped:
                    skipped.append(f"{kind}/{sec}")
                continue
            is_new, name = new.get((kind, tid), (False, None))
            ctx = Ctx(session, cr, ver, now, by, is_new, name)
            dirty |= await hook(ctx, tid, rows)
    await session.flush()
    return skipped


# ---------------------------------------------------------------- 业务知识
K_FIELDS = ("name", "type", "definition", "calc", "synonyms", "period", "owner", "applies")


async def _knowledge(ctx: Ctx, tid: str, *, create: bool) -> Knowledge | None:
    k = await ctx.session.scalar(select(Knowledge).where(Knowledge.code == tid))
    if k is None and create:
        seq = max((x.seq for x in await ctx.session.scalars(select(Knowledge))), default=0) + 1
        k = Knowledge(
            code=tid,
            name=tid,
            type=KnowledgeType.term,
            status=KnowledgeStatus.published,
            owner=ctx.cr.owner,
            content_updated_at=ctx.now,
            history=[],
            applies=[],
            seq=seq,
            created_by=ctx.by,
        )
        ctx.session.add(k)
        await ctx.session.flush()
    if k is None:
        raise _bad(f"业务知识 {tid} 不存在，不能写回", kind="knowledge", id=tid)
    return k


def _history(ctx: Ctx, k: Knowledge, change: str, status: str = "生效中") -> None:
    old = [{**h, "status": "已替换"} if h.get("status") == "生效中" else h for h in k.history or []]
    row = {"ver": ctx.ver, "cr": ctx.cr.code, "status": status, "by": ctx.cr.owner, "at": _fmt_dt(ctx.now)}
    k.history = [{**row, "change": change}, *old]


@register("knowledge", "content")
async def knowledge_content(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    k = await _knowledge(ctx, tid, create=True)
    assert k is not None
    fresh = k.since_release is None or k.status == KnowledgeStatus.draft
    changed: list[str] = []
    for r in rows:
        key, v = r.item_key, r.after
        if key not in K_FIELDS or r.op == "delete":
            continue
        if key == "type":
            k.type = _label(KnowledgeType, v, "类型")
        elif key == "synonyms":
            k.synonyms = [str(x) for x in (v or [])]
        elif key == "applies":
            for a in list(k.applies):
                k.applies.remove(a)
                await ctx.session.delete(a)
            await ctx.session.flush()
            for n, a in enumerate(v or [], start=1):
                k.applies.append(
                    KnowledgeApply(
                        target_kind=_label(ApplyKind, a.get("kind"), "适用范围"),
                        target_code=str(a.get("id")),
                        target_name=str(a.get("name") or a.get("id")),
                        seq=n,
                        created_by=ctx.by,
                    )
                )
        else:
            setattr(k, key, "" if v is None else str(v))
        changed.append(r.field_label or key)
    if fresh:
        k.status = KnowledgeStatus.published
        k.since_release = ctx.ver
    k.cr_code = ctx.cr.code
    k.draft = None
    k.content_updated_at = ctx.now
    _history(ctx, k, ("创建：" if fresh else "修改：") + "、".join(changed))
    return {"terms"}


@register("knowledge", "status")
async def knowledge_status(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    k = await _knowledge(ctx, tid, create=False)
    assert k is not None
    for r in rows:
        if r.item_key != "status" or r.op == "delete":
            continue
        st = _label(KnowledgeStatus, r.after, "状态")
        k.status = st
        k.stopped_release = ctx.ver if st == KnowledgeStatus.disabled else None
        k.cr_code = ctx.cr.code
        k.content_updated_at = ctx.now
        _history(ctx, k, f"状态改为{st.value}", "已停用" if st == KnowledgeStatus.disabled else "生效中")
    return {"terms"}


# ---------------------------------------------------------------- 对象属性
P_FIELDS = {
    "name": "display_name",
    "type": "data_type",
    "group": "prop_group",
    "source": "source_column",
    "desc": "description",
    "enumName": "enum_name",
}
P_FLAGS = {"pk": "is_pk", "title": "is_title", "dim": "is_dimension", "sensitive": "is_sensitive"}


def _set_prop(p: Property, after: dict[str, Any]) -> None:
    for k, col in P_FIELDS.items():
        if k in after:
            setattr(p, col, after[k] if k == "enumName" else str(after[k] or ""))
    for k, col in P_FLAGS.items():
        if k in after:
            setattr(p, col, bool(after[k]))


@register("object", "properties")
async def object_properties(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    o = await _object(ctx, tid)
    by_api = {p.api_name: p for p in o.properties}
    for r in rows:
        after = r.after if isinstance(r.after, dict) else {}
        p = by_api.get(r.item_key)
        if r.op == "delete":
            if p is not None:
                o.properties.remove(p)
                await ctx.session.delete(p)
                by_api.pop(r.item_key)
            continue
        if p is None:
            if r.op != "add":
                raise _bad(f"属性 {tid}.{r.item_key} 不存在，不能修改", kind="object", id=tid, key=r.item_key)
            p = Property(
                api_name=r.item_key,
                display_name=str(after.get("name") or r.item_key),
                data_type=str(after.get("type") or "string"),
                prop_group=str(after.get("group") or "其他"),
                source_column=str(after.get("source") or ""),
                description=str(after.get("desc") or ""),
                seq=max((x.seq for x in o.properties), default=0) + 1,
                created_by=ctx.by,
            )
            o.properties.append(p)
            by_api[r.item_key] = p
        _set_prop(p, after)
    return {"properties"}


# ---------------------------------------------------------------- 对象类型（新建 / 配置）
O_FIELDS = {
    "name": "display_name",
    "api": "api_name",
    "group": "obj_group",
    "domain": "domain",
    "desc": "description",
    "owner": "owner",
    "table": "source_table",
    "source": "datasource_name",
    "pk": "pk_property",
    "sync": "sync",
}


async def _object(ctx: Ctx, tid: str) -> ObjectType:
    """取对象类型；本单新建（cr_target.is_new）且库里没有时按默认值建一个，再由 def / properties 行补字段。"""
    o = await ctx.session.scalar(select(ObjectType).where(ObjectType.code == tid))
    if o is not None:
        return o
    if not ctx.is_new:
        raise _bad(f"对象类型 {tid} 不存在（目标没标 isNew），不能写回", kind="object", id=tid)
    seq = max((x.seq for x in await ctx.session.scalars(select(ObjectType))), default=0) + 1
    o = ObjectType(
        code=tid,
        api_name=tid,
        display_name=ctx.name or tid,
        obj_group="其他",
        description="",
        conflict_strategy=ConflictStrategy.user_edit_wins,
        lifecycle=Lifecycle.active,
        status=ObjectStatus.published,
        owner=ctx.cr.owner,
        properties=[],
        seq=seq,
        created_by=ctx.by,
    )
    ctx.session.add(o)
    await ctx.session.flush()
    return o


async def _object_def(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    o = await _object(ctx, tid)
    for r in rows:
        if r.op == "delete":
            continue
        if r.item_key == "conflict":
            o.conflict_strategy = _label(ConflictStrategy, r.after, "冲突策略")
        elif r.item_key in O_FIELDS:
            setattr(o, O_FIELDS[r.item_key], None if r.after is None else str(r.after))
    o.status = ObjectStatus.published
    return {"objectTypes"}


register("object", "def")(_object_def)
register("object", "config")(_object_def)


# ---------------------------------------------------------------- 关系类型（def 段，一个字段一行）
L_FIELDS = {
    "name": "display_name",
    "api": "api_name",
    "fromProp": "from_prop",
    "toProp": "to_prop",
    "via": "via",
    "reverse": "reverse_name",
    "desc": "description",
}


@register("link", "def")
async def link_def(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    s = ctx.session
    v = {r.item_key: r.after for r in rows if r.op != "delete"}
    lk = await s.scalar(select(LinkType).where(LinkType.code == tid))
    if lk is None:
        if not ctx.is_new:
            raise _bad(f"关系类型 {tid} 不存在（目标没标 isNew），不能写回", kind="link", id=tid)
        frm = v.get("from")
        if not frm or not v.get("to"):
            raise _bad(f"新建关系 {tid} 缺少 from / to", kind="link", id=tid)
        seq = max((x.seq for x in await s.scalars(select(LinkType))), default=0) + 1
        lk = LinkType(
            code=tid,
            display_name=ctx.name or tid,
            from_code=str(frm),
            to_code=str(v["to"]),
            cardinality=Cardinality.many_to_one,
            key_expr="",
            from_prop="",
            to_prop="",
            join_sql="",
            reverse_name="",
            nullable=False,
            description="",
            status=DefStatus.published,
            owner=ctx.cr.owner,
            usage=[],
            history=[],
            seq=seq,
            created_by=ctx.by,
        )
        s.add(lk)
    for k, val in v.items():
        if k in L_FIELDS:
            setattr(lk, L_FIELDS[k], (val or None) if k == "via" else str(val or ""))
        elif k == "card":
            lk.cardinality = _label(Cardinality, val, "基数")
        elif k == "nullable":
            lk.nullable = bool(val)
        elif k in ("from", "to"):
            setattr(lk, f"{k}_code", str(val))
    objs = {o.code: o.id for o in await s.scalars(select(ObjectType))}
    lk.from_object_id, lk.to_object_id = objs.get(lk.from_code), objs.get(lk.to_code)
    if lk.cardinality == Cardinality.many_to_many:
        lk.key_expr = f"经 {lk.via or '（未选择）'} 中间表"
    else:
        lk.key_expr = f"{lk.from_prop} = {lk.to_code}.{lk.to_prop}" if lk.to_code else ""
        lk.join_sql = f"{lk.from_code}.{lk.from_prop} = {lk.to_code}.{lk.to_prop}"
    if lk.status != DefStatus.published:
        lk.status = DefStatus.published
    lk.since_release = lk.since_release or ctx.ver
    lk.cr_code = ctx.cr.code
    row = {"ver": ctx.ver, "cr": ctx.cr.code, "by": ctx.cr.owner, "at": _fmt_dt(ctx.now)}
    lk.history = [
        {**row, "change": "、".join(r.field_label or r.item_key for r in rows)},
        *(lk.history or []),
    ]
    await s.flush()
    return {"links"}


# ---------------------------------------------------------------- 指标（def 段，一个字段一行）
M_FIELDS = {
    "name": "display_name",
    "api": "api_name",
    "group": "metric_group",
    "unit": "unit",
    "formula": "formula",
    "owner": "owner",
    "version": "version",
}


@register("metric", "def")
async def metric_def(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    s = ctx.session
    m = await s.scalar(select(Metric).where(Metric.code == tid))
    if m is None:
        if not ctx.is_new:
            raise _bad(f"指标 {tid} 不存在（目标没标 isNew），不能写回", kind="metric", id=tid)
        seq = max((x.seq for x in await s.scalars(select(Metric))), default=0) + 1
        m = Metric(
            code=tid,
            api_name=tid,
            display_name=ctx.name or tid,
            metric_group="其他",
            unit="",
            version="v1",
            status=MetricStatus.published,
            owner=ctx.cr.owner,
            seq=seq,
            created_by=ctx.by,
        )
        s.add(m)
    for r in rows:
        k, val = r.item_key, r.after
        if r.op == "delete" and k in ("formula", "dims"):
            setattr(m, k, None)
        elif r.op == "delete":
            continue
        elif k in M_FIELDS:
            setattr(m, M_FIELDS[k], None if val is None and k == "formula" else str(val or ""))
        elif k == "dims":
            m.dims = [str(x) for x in val or []]
        elif k == "status":
            m.status = _label(MetricStatus, val, "状态")
    m.review_cr = None if m.review_cr == ctx.cr.code else m.review_cr
    await s.flush()
    return {"metrics"}


# ---------------------------------------------------------------- 维度：取值 / 说法 / 问数设置
async def _dim(ctx: Ctx, tid: str) -> Dimension:
    d = await ctx.session.scalar(select(Dimension).where(Dimension.code == tid))
    if d is None:
        raise _bad(f"维度 {tid} 不存在，新建维度暂不支持发布写回", kind="dimension", id=tid)
    return d


def _set_value(v: DimValue, a: dict[str, Any]) -> None:
    for k in ("code", "name", "parent", "note"):
        if k in a:
            setattr(v, k, a[k] if k in ("parent", "note") else str(a[k]))
    if "level" in a:
        v.level_name = str(a["level"])
    if "synonyms" in a:
        v.synonyms = [str(x) for x in a["synonyms"] or []]
    if "status" in a:
        v.status = _label(DimValueStatus, a["status"], "取值状态")


@register("dimension", "values")
async def dimension_values(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    s = ctx.session
    d = await _dim(ctx, tid)
    have = {v.value_key: v for v in await s.scalars(select(DimValue).where(DimValue.dimension_id == d.id))}
    seq = max((v.seq for v in have.values()), default=0)
    for r in rows:
        a = r.after if isinstance(r.after, dict) else {}
        v = have.get(r.item_key)
        if r.op == "delete":
            if v is not None:
                v.until_release = ctx.ver
            continue
        if v is None:
            if r.op != "add":
                raise _bad(
                    f"取值 {tid}.{r.item_key} 不存在，不能修改", kind="dimension", id=tid, key=r.item_key
                )
            level, _, code = r.item_key.partition(":")
            seq += 1
            v = DimValue(
                dimension_id=d.id,
                value_key=r.item_key,
                code=str(a.get("code") or code),
                name=str(a.get("name") or code),
                level_name=str(a.get("level") or level),
                status=DimValueStatus.enabled,
                synonyms=[],
                since_release=ctx.ver,
                seq=seq,
                created_by=ctx.by,
            )
            s.add(v)
            have[r.item_key] = v
        _set_value(v, a)
        v.draft_cr, v.diff, v.pub = None, None, None
    await s.flush()
    return {"dimensions"}


@register("dimension", "mappings")
async def dimension_mappings(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    s = ctx.session
    d = await _dim(ctx, tid)
    have = {m.map_key: m for m in await s.scalars(select(DimMapping).where(DimMapping.dimension_id == d.id))}
    seq = max((m.seq for m in have.values()), default=0)
    for r in rows:
        a = r.after if isinstance(r.after, dict) else {}
        m = have.get(r.item_key)
        if r.op == "delete":
            if m is not None:
                await s.delete(m)
            continue
        if m is None:
            seq += 1
            m = DimMapping(
                dimension_id=d.id,
                map_key=r.item_key,
                phrase=str(a.get("phrase") or r.item_key.split(">")[0]),
                targets=[],
                match=MatchMode.exact,
                source=MappingSource.knowledge,
                seq=seq,
                created_by=ctx.by,
            )
            s.add(m)
            have[r.item_key] = m
        if "phrase" in a:
            m.phrase = str(a["phrase"])
        if "targets" in a:
            m.targets = [str(x) for x in a["targets"] or []]
        if "match" in a:
            m.match = _label(MatchMode, a["match"], "匹配方式")
        if "source" in a:
            m.source = _label(MappingSource, a["source"], "来源")
        if "resolve" in a:
            m.resolve = a["resolve"]
        m.draft_cr, m.diff = None, None
    await s.flush()
    return {"dimensions"}


@register("dimension", "qa")
async def dimension_qa(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    d = await _dim(ctx, tid)
    qa = dict(d.qa or {})
    for r in rows:
        if r.item_key == "desc":
            d.description = str(r.after or "")
        elif r.op == "delete":
            qa.pop(r.item_key, None)
        else:
            qa[r.item_key] = r.after
    d.qa = qa
    return {"dimensions"}


# ---------------------------------------------------------------- 整版回滚：terms 写回定义表
async def restore_terms(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """TermsSection.restore_section：把 knowledge 表恢复成被恢复版本快照里的 terms。

    - 快照里有的条目：内容字段、状态、since / stoppedIn 按快照恢复；history 保留现状再加一行「回滚恢复」。
    - 快照里没有、表里已发布的条目：置已停用（stoppedIn = 这次回滚的版本）；草稿条目不动。
    - 快照里有、表里已删除的条目：不重建（知识不支持物理删除，正常不会出现），记 warning。
    """
    from app.core import clock
    from app.modules.versioning.models import OntologyRelease

    rel = await session.get(OntologyRelease, release_id)
    ver = rel.ver if rel is not None else None
    now = clock.now()
    row = {"ver": ver or "—", "cr": "", "by": "系统", "at": _fmt_dt(now)}
    for k in await session.scalars(select(Knowledge)):
        e = content.get(k.code)
        if e is None:
            if k.status == KnowledgeStatus.published:
                k.status = KnowledgeStatus.disabled
                k.stopped_release = ver
                gone = {**row, "status": "已停用", "change": "回滚到无此条目的版本，停用"}
                k.history = [gone, *(k.history or [])]
                k.content_updated_at = now
            continue
        for key in ("name", "definition", "calc", "period", "owner"):
            if key in e:
                setattr(k, key, "" if e[key] is None else str(e[key]))
        if "type" in e:
            k.type = _label(KnowledgeType, e["type"], "类型")
        if "synonyms" in e:
            k.synonyms = [str(x) for x in e["synonyms"] or []]
        if "applies" in e:
            for a in list(k.applies):
                k.applies.remove(a)
                await session.delete(a)
            await session.flush()
            for n, a in enumerate(e["applies"] or [], start=1):
                k.applies.append(
                    KnowledgeApply(
                        target_kind=_label(ApplyKind, a.get("kind"), "适用范围"),
                        target_code=str(a.get("id")),
                        target_name=str(a.get("name") or a.get("id")),
                        seq=n,
                        created_by=k.created_by,
                    )
                )
        k.status = _label(KnowledgeStatus, e.get("status") or "已发布", "状态")
        k.since_release = e.get("since") or k.since_release
        k.stopped_release = e.get("stoppedIn")
        k.cr_code = e.get("cr", k.cr_code)
        k.content_updated_at = now
        k.history = [{**row, "status": "生效中", "change": "回滚恢复"}, *(k.history or [])]
    await session.flush()


# ---------------------------------------------------------------- 整版回滚：properties / links / dimensions
async def _restore_ver(session: AsyncSession, release_id: int) -> str:
    from app.modules.versioning.models import OntologyRelease

    rel = await session.get(OntologyRelease, release_id)
    return rel.ver if rel is not None else "—"


async def restore_properties(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """PropertiesSection.restore_section：快照里有的对象，属性按快照覆盖（多的删、少的补、字段回写）。

    快照里没有的对象（之后新建的）不动——对象类型节不回滚表。
    """
    for o in await session.scalars(select(ObjectType)):
        snap = content.get(o.code)
        if not isinstance(snap, dict):
            continue
        by_api = {p.api_name: p for p in o.properties}
        for api, p in list(by_api.items()):
            if api not in snap:
                o.properties.remove(p)
                await session.delete(p)
        seq = max((x.seq for x in o.properties), default=0)
        for api, after in snap.items():
            p = by_api.get(api)
            if p is None:
                seq += 1
                p = Property(
                    api_name=api,
                    display_name=str(after.get("name") or api),
                    data_type=str(after.get("type") or "string"),
                    prop_group=str(after.get("group") or "其他"),
                    source_column=str(after.get("source") or ""),
                    description=str(after.get("desc") or ""),
                    seq=seq,
                    created_by="系统",
                )
                o.properties.append(p)
            _set_prop(p, {**dict.fromkeys(P_FLAGS, False), "enumName": None, **after})
    await session.flush()


async def restore_links(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """LinksSection.restore_section：快照里有的关系字段按快照回写；表里已发布但快照没有的置草稿（不再出现在快照节）。

    快照里有、表里没有的（物理删除过）不重建，记 warning。
    """
    ver = await _restore_ver(session, release_id)
    have = {x.code: x for x in await session.scalars(select(LinkType))}
    for code, d in content.items():
        lk = have.get(code)
        if lk is None:
            log.warning("restore_links: 关系 %s 表里不存在，跳过", code)
            continue
        lk.display_name, lk.api_name = d.get("name", lk.display_name), d.get("api")
        lk.from_code, lk.to_code = d.get("from", lk.from_code), d.get("to", lk.to_code)
        lk.cardinality = Cardinality(d.get("card", lk.cardinality.value))
        lk.key_expr, lk.join_sql = d.get("key", lk.key_expr), d.get("join", lk.join_sql)
        lk.from_prop, lk.to_prop, lk.via = d.get("fromProp", ""), d.get("toProp", ""), d.get("via")
        lk.reverse_name, lk.nullable = d.get("reverse", ""), bool(d.get("nullable"))
        lk.description, lk.owner = d.get("desc", ""), d.get("owner", lk.owner)
        lk.status, lk.since_release, lk.cr_code = DefStatus.published, d.get("since"), d.get("cr")
        lk.history = [{"ver": ver, "cr": "", "by": "系统", "change": "回滚恢复"}, *(d.get("history") or [])]
    for code, lk in have.items():
        if code not in content and lk.status == DefStatus.published:
            lk.status = DefStatus.draft
    await session.flush()


async def restore_dimensions(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """DimensionsSection.restore_section：只回写取值 / 说法 / 问数设置 / 描述（维度本身的增删不回滚）。

    - 取值：快照里有的按快照回写并清 until；表里在用、快照没有的置 until = 这次回滚的版本。
    - 说法：按快照覆盖（多的删、少的补）。
    """
    ver = await _restore_ver(session, release_id)
    for d in await session.scalars(select(Dimension)):
        snap = content.get(d.code)
        if not isinstance(snap, dict):
            continue
        d.description = snap.get("desc", d.description)
        if isinstance(snap.get("qa"), dict):
            d.qa = dict(snap["qa"])
        vals = {
            v.value_key: v
            for v in await session.scalars(select(DimValue).where(DimValue.dimension_id == d.id))
        }
        want = {v["key"]: v for v in snap.get("values") or [] if "key" in v}
        seq = max((v.seq for v in vals.values()), default=0)
        for k, v in vals.items():
            if k not in want and v.until_release is None:
                v.until_release = ver
        for k, a in want.items():
            v = vals.get(k)
            if v is None:
                seq += 1
                v = DimValue(
                    dimension_id=d.id,
                    value_key=k,
                    code=str(a.get("code", "")),
                    name=str(a.get("name", "")),
                    level_name=str(a.get("level", "")),
                    status=DimValueStatus.enabled,
                    synonyms=[],
                    seq=seq,
                    created_by="系统",
                )
                session.add(v)
            _set_value(v, {"parent": None, "note": None, **a})
            v.since_release, v.until_release = a.get("since"), a.get("until")
            v.draft_cr, v.diff, v.pub = None, None, None
        maps = {
            m.map_key: m
            for m in await session.scalars(select(DimMapping).where(DimMapping.dimension_id == d.id))
        }
        mwant = {m["key"]: m for m in snap.get("mappings") or [] if "key" in m}
        for k, m in maps.items():
            if k not in mwant:
                await session.delete(m)
        seq = max((m.seq for m in maps.values()), default=0)
        for k, a in mwant.items():
            m = maps.get(k)
            if m is None:
                seq += 1
                m = DimMapping(
                    dimension_id=d.id,
                    map_key=k,
                    phrase=str(a.get("phrase", "")),
                    match=MatchMode.exact,
                    source=MappingSource.knowledge,
                    seq=seq,
                    created_by="系统",
                )
                session.add(m)
            m.phrase, m.targets = str(a.get("phrase", m.phrase)), list(a.get("targets") or [])
            m.match = MatchMode(a.get("match", m.match.value))
            m.source = MappingSource(a.get("source", m.source.value))
            m.resolve, m.conflict = a.get("resolve"), a.get("conflict")
            m.draft_cr, m.diff = None, None
    await session.flush()
