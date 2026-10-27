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

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.ontology import require_ontology_id
from app.core.errors import AppError
from app.core.schema import _fmt_dt
from app.modules.admin.permissions import resolve_owner_name
from app.modules.static.dimension_validation import dimension_source_error
from app.modules.static.explorer import DISPLAY_FIELDS, LONG_TEXT, VISIBILITY, snapshot_flags
from app.modules.static.models import (
    ApplyKind,
    Cardinality,
    ChangeRequest,
    ConflictStrategy,
    CrItem,
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
    LinkType,
    MappingSource,
    MatchMode,
    Metric,
    MetricStatus,
    ObjectStatus,
    ObjectType,
    Property,
    QaPath,
    QaPathExample,
    QaPathStep,
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


def register_writeback(kind: str, section: str, fn: Hook, *, replace: bool = False) -> Hook:
    """公开登记入口：其他模块（动态 / Palantir）在自己的模块里调用，把自己的 kind/section 写回挂进整版发布。

    - fn 签名 `async (ctx: Ctx, target_id: str, rows: list[CrItem]) -> set[str]`，在发布事务里执行，
      只能改自己模块的表；返回改到的**静态**快照节名（不改静态节返回空集合）。
    - 出错抛 `writeback_error(msg, **details)`（409 WRITEBACK_FAILED），整个发布回滚。
    - 同一 (kind, section) 已登记 → ValueError（防止覆盖静态钩子）；确需替换传 replace=True。
    - 要在 import 时登记（模块加载即生效）；发布按 (kind, section) 查表，登记先后不影响执行顺序。
    """
    if (kind, section) in HOOKS and not replace and HOOKS[(kind, section)] is not fn:
        raise ValueError(f"写回钩子 {kind}/{section} 已登记")
    HOOKS[(kind, section)] = fn
    return fn


def writeback_error(msg: str, **details: Any) -> AppError:
    """钩子里报错用：409 WRITEBACK_FAILED，details 原样返回给前端。"""
    return _bad(msg, **details)


def _bad(msg: str, **details: Any) -> AppError:
    return AppError(msg, code="WRITEBACK_FAILED", status_code=409, details=details)


async def owner_name(ctx: Ctx, value: Any) -> str:
    try:
        return await resolve_owner_name(ctx.session, value)
    except AppError as error:
        raise _bad(error.message, field="owner") from error


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
        # Same-CR relations may reference newly saved objects, regardless of editing order.
        ordered = sorted(
            groups.items(),
            key=lambda item: (
                item[0][0] != "object",
                item[0][2] not in ("def", "config") if item[0][0] == "object" else False,
            ),
        )
        for (kind, tid, sec), rows in ordered:
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
P_TYPES = {"string", "long_text", "int", "integer", "decimal", "date", "timestamp", "boolean", "enum", "json"}


def _prop_type(value: Any, api: str) -> str:
    if not isinstance(value, str) or value not in P_TYPES:
        raise _bad("属性类型不合法", key=api, field="type")
    return value


def _set_prop(p: Property, after: dict[str, Any]) -> None:
    if "type" in after:
        _prop_type(after["type"], p.api_name)
    for k, col in P_FIELDS.items():
        if k in after:
            setattr(p, col, after[k] if k == "enumName" else str(after[k] or ""))
    for k, col in P_FLAGS.items():
        if k in after:
            setattr(p, col, bool(after[k]))
    for key, col in DISPLAY_FIELDS.items():
        if key not in after:
            continue
        value = after[key]
        valid = True
        if key in ("searchable", "filterable", "sortable", "listed"):
            valid = isinstance(value, bool)
        elif key == "visibility":
            valid = value in VISIBILITY
        elif key in ("order", "listWidth"):
            valid = (key == "listWidth" and value is None) or (
                type(value) is int and (0 if key == "order" else 1) <= value <= 2147483647
            )
        elif key in ("format", "unit"):
            valid = value is None or (
                isinstance(value, str)
                and len(value) <= (32 if key == "format" else 16)
                and "\x00" not in value
            )
        if not valid:
            raise _bad(f"属性显示标记 {key} 不合法", key=p.api_name, field=key)
        setattr(p, col, value)
    if p.visibility == "hidden":
        p.filterable = p.sortable = p.list_visible = False
    if p.is_sensitive:
        p.filterable = False
    if p.data_type == LONG_TEXT:  # 长文本只在详情单独成区，不做分面 / 排序（generic-object-explorer §4）
        p.filterable = p.sortable = False


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
            typ = _prop_type(after.get("type", "string"), r.item_key)
            p = Property(
                api_name=r.item_key,
                display_name=str(after.get("name") or r.item_key),
                data_type=typ,
                prop_group=str(after.get("group") or "其他"),
                source_column=str(after.get("source") or ""),
                description=str(after.get("desc") or ""),
                **snapshot_flags(
                    tid,
                    {**after, "api": r.item_key, "type": typ},
                    max((x.seq for x in o.properties), default=0) + 1,
                    require_ontology_id(ctx.session),
                ),
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
    if any(r.item_key in ("source", "table") and r.op != "delete" for r in rows):
        from app.modules.data.mappings import lock_source

        source = next(
            (r.after for r in reversed(rows) if r.item_key == "source" and r.op != "delete"),
            o.datasource_name,
        )
        await lock_source(ctx.session, source)
    for r in rows:
        if r.op == "delete":
            continue
        if r.item_key == "conflict":
            o.conflict_strategy = _label(ConflictStrategy, r.after, "冲突策略")
        elif r.item_key == "owner":
            from app.modules.admin.permissions import resolve_owner_name

            try:
                o.owner = await resolve_owner_name(ctx.session, r.after)
            except AppError as e:
                raise _bad(e.message, kind="object", id=tid, field="owner") from None
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
    from app.modules.static.link_validation import link_error, modeling_objects
    from app.modules.static.shapes import link

    objects, properties = await modeling_objects(s)
    error = link_error(
        {**(link(lk, with_runtime=False) if lk is not None else {}), **v},
        objects,
        properties,
        previous_via=lk.via if lk else None,
        is_new=lk is None,
    )
    if error:
        raise _bad(error, kind="link", id=tid)
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
    from app.modules.static.metric_versions import EXEC_KEYS, EXEC_MAX, definition, record_publish, with_exec

    s = ctx.session
    m = await s.scalar(select(Metric).where(Metric.code == tid))
    before, previous = (definition(m), m.version) if m is not None else (None, None)
    requested = next((r.after for r in reversed(rows) if r.item_key == "version" and r.op != "delete"), None)
    if any(r.item_key == "version" and r.op != "delete" for r in rows):
        from app.modules.static.metric_versions import VERSION

        if not isinstance(requested, str) or VERSION.fullmatch(requested) is None:
            raise _bad("口径版本须为v加正整数，且不超过16字", kind="metric", id=tid)
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
        if k == "variants":
            val = [] if r.op == "delete" else val
            if not isinstance(val, list) or len(val) > 32:
                raise _bad("指标变体必须是至多 32 行的列表", id=tid)
            variants = []
            codes, labels = set(), set()
            targets = {x.code: x for x in await s.scalars(select(Metric))}
            for v in val:
                if not isinstance(v, dict):
                    raise _bad("指标变体行必须是对象", id=tid)
                code, label = v.get("code"), v.get("label")
                if (
                    not isinstance(code, str)
                    or not 1 <= len(code) <= 64
                    or not isinstance(label, str)
                    or not 1 <= len(label.strip()) <= 64
                    or not isinstance(v.get("default"), bool)
                    or code in codes
                    or label.strip() in labels
                ):
                    raise _bad("指标变体编码、标签或默认标记不合法，且编码和标签不能重复", id=tid)
                target = targets.get(code)
                if target is None:
                    raise _bad("指标变体引用的指标不存在", id=tid, metric=code)
                codes.add(code)
                labels.add(label.strip())
                variants.append(
                    {
                        "code": code,
                        "label": label.strip(),
                        "default": v["default"],
                        "api": target.api_name,
                        "version": target.version,
                    }
                )
            if variants and sum(v["default"] for v in variants) != 1:
                raise _bad("指标变体必须且只能有一个默认项", id=tid)
            m.variants = variants
        elif k in EXEC_KEYS:
            # 时间字段 / 默认过滤：只存明确文本，执行含义由试算编译器校验，这里不猜也不改写。
            if r.op != "delete" and (
                not isinstance(val, str) or not val.strip() or len(val) > EXEC_MAX or "\x00" in val
            ):
                raise _bad(
                    f"{EXEC_KEYS[k]}须为非空文本，且不超过{EXEC_MAX}字", kind="metric", id=tid, field=k
                )
            m.def_rows = with_exec(m.def_rows, k, None if r.op == "delete" else val.strip())
        elif r.op == "delete" and k in ("formula", "dims"):
            setattr(m, k, None)
        elif r.op == "delete":
            continue
        elif k == "owner":
            m.owner = await owner_name(ctx, val)
        elif k == "version":
            continue  # 先验证和分配，不把非法/超长版本提前flush进数据库。
        elif k in M_FIELDS:
            maximum = {"name": 64, "api": 64, "group": 32, "unit": 16}.get(k)
            if not (k == "formula" and val is None) and (
                not isinstance(val, str) or (maximum is not None and len(val) > maximum)
            ):
                raise _bad("指标字段类型或长度不合法", kind="metric", id=tid, field=k)
            if k in ("name", "api") and not val.strip():
                raise _bad("指标名称和API名不能为空", kind="metric", id=tid, field=k)
            if k == "api":
                with s.no_autoflush:
                    duplicate = await s.scalar(
                        select(Metric.code).where(Metric.api_name == val, Metric.code != tid).limit(1)
                    )
                if duplicate:
                    raise _bad("指标API名已被其他指标使用", kind="metric", id=tid, field=k)
            setattr(m, M_FIELDS[k], None if val is None and k == "formula" else str(val or ""))
        elif k == "dims":
            if val is not None and (
                not isinstance(val, list) or any(not isinstance(v, str) or len(v) > 64 for v in val)
            ):
                raise _bad("允许维度须为文本列表，每项不超过64字", kind="metric", id=tid, field=k)
            m.dims = [str(x) for x in val or []]
        elif k == "status":
            m.status = _label(MetricStatus, val, "状态")
    m.review_cr = None if m.review_cr == ctx.cr.code else m.review_cr
    try:
        await record_publish(ctx, m, before, previous, requested)
    except AppError as error:
        raise _bad(error.message, kind="metric", id=tid, cause=error.code) from None
    await s.flush()
    return {"metrics"}


# ---------------------------------------------------------------- 维度：取值 / 说法 / 问数设置
D_FIELDS = {
    "name": "display_name",
    "api": "api_name",
    "group": "dim_group",
    "owner": "owner",
    "attr": "attr",
    "column": "source_column",
    "valueSrc": "value_src",
    "sync": "sync",
    "desc": "description",
}


async def _dimension_source(session, code, kind, object_code, *, attr="", column=""):
    obj = (
        await session.scalar(select(ObjectType).where(ObjectType.code == object_code))
        if isinstance(object_code, str) and object_code
        else None
    )
    error = dimension_source_error(
        kind, object_code, {obj.code} if obj is not None else set(), attr=attr, column=column
    )
    if error:
        raise _bad(error, kind="dimension", id=code, key="object")
    return obj


async def _dim(ctx: Ctx, tid: str) -> Dimension:
    """取维度；本单新建（isNew）且库里没有时，按本单 dimension/def 段的行新建（values 先到也能建）。"""
    s = ctx.session
    d = await s.scalar(select(Dimension).where(Dimension.code == tid))
    if d is not None:
        return d
    if not ctx.is_new:
        raise _bad(f"维度 {tid} 不存在（目标没标 isNew），不能写回", kind="dimension", id=tid)
    q = select(CrItem).where(
        CrItem.cr_id == ctx.cr.id,
        CrItem.target_kind == "dimension",
        CrItem.target_id == tid,
        CrItem.section.in_(("def", "config")),
    )
    v = {r.item_key: r.after for r in await s.scalars(q.order_by(CrItem.seq, CrItem.id)) if r.op != "delete"}
    kind = _label(DimKind, v.get("kind") or "维度", "类型")
    o = await _dimension_source(
        s, tid, kind.value, v.get("object", ""), attr=v.get("attr", ""), column=v.get("column", "")
    )
    levels = [x if isinstance(x, dict) else {"name": str(x)} for x in v.get("levels") or []]
    seq = max((x.seq for x in await s.scalars(select(Dimension))), default=0) + 1
    d = Dimension(
        code=tid,
        api_name=str(v.get("api") or tid),
        display_name=str(v.get("name") or ctx.name or tid),
        kind=kind,
        dim_group=str(v.get("group") or "其他"),
        status=DefStatus.published,
        owner=str(v.get("owner") or ctx.cr.owner),
        object=o,
        object_name=o.display_name if o is not None else "",
        attr="",
        source_column="",
        value_src="",
        sync="",
        description="",
        qa={},
        since_release=ctx.ver,
        history=[
            {"ver": ctx.ver, "cr": ctx.cr.code, "by": ctx.cr.owner, "at": _fmt_dt(ctx.now), "change": "新建"}
        ],
        levels=[
            DimLevel(
                level_no=i + 1,
                name=str(lv.get("name") or f"层级{i + 1}"),
                api_name=str(lv.get("api") or lv.get("name") or f"l{i + 1}"),
                source=str(lv.get("source") or ""),
                created_by=ctx.by,
            )
            for i, lv in enumerate(levels)
        ],
        seq=seq,
        created_by=ctx.by,
    )
    s.add(d)
    await s.flush()
    return d


def _dim_level_rows(code: str, value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise _bad("维度层级须为至多 32 行的列表", id=code)
    desired = []
    apis, names = set(), set()
    for item in value:
        item = {"name": item, "api": item} if isinstance(item, str) else item
        if not isinstance(item, dict):
            raise _bad("维度层级行不合法", id=code)
        row = {
            "name": item.get("name"),
            "api": item.get("api") or item.get("name"),
            "source": item.get("source") or "",
            "note": item.get("note"),
        }
        for key, limit in (("name", 64), ("api", 64), ("source", 256), ("note", 4000)):
            v = row[key]
            if key == "note" and v is None:
                continue
            if not isinstance(v, str) or len(v) > limit or (key in {"name", "api"} and not v.strip()):
                raise _bad("维度层级字段不合法", id=code, field=key)
        if row["api"] in apis or row["name"] in names:
            raise _bad("维度层级 API 名和名称不能重复", id=code)
        apis.add(row["api"])
        names.add(row["name"])
        desired.append(row)
    return desired


def _check_restored_value_levels(code: str, levels: list[dict[str, Any]], values: Any) -> None:
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        raise _bad("恢复快照的维度取值须为对象列表", id=code)
    keys = [row.get("key") for row in values]
    if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
        raise _bad("恢复快照的取值键为空或重复", id=code)
    names = {row["name"] for row in levels}
    invalid = [
        row["key"]
        for row in values
        if not row.get("until") and (not isinstance(row.get("level"), str) or row["level"] not in names)
    ]
    if invalid:
        raise _bad("恢复快照的有效取值引用了不存在的层级", id=code, values=invalid)


async def _set_dim_levels(
    session: AsyncSession, d: Dimension, value: Any, by: str, *, replacement_values: list[dict] | None = None
) -> None:
    desired = _dim_level_rows(d.code, value)
    apis = {row["api"] for row in desired}
    old = {level.api_name: level for level in d.levels}
    names_by_api = {row["api"]: row["name"] for row in desired}
    removed = [level.name for api, level in old.items() if names_by_api.get(api) != level.name]
    if replacement_values is not None:
        _check_restored_value_levels(d.code, desired, replacement_values)
    elif removed and await session.scalar(
        select(DimValue.id)
        .where(
            DimValue.dimension_id == d.id, DimValue.level_name.in_(removed), DimValue.until_release.is_(None)
        )
        .limit(1)
    ):
        raise _bad("层级仍有有效取值，请先移除或迁移取值", id=d.code)
    for i, (api, level) in enumerate(old.items(), 1):
        if api not in apis:
            await session.delete(level)
        else:
            level.level_no = -i
    await session.flush()
    levels = []
    for no, row in enumerate(desired, 1):
        level = old.get(row["api"])
        if level is None:
            level = DimLevel(dimension_id=d.id, api_name=row["api"], value_count=0, created_by=by)
        level.level_no, level.name = no, row["name"]
        level.source, level.note = row["source"], row["note"]
        levels.append(level)
    d.levels = levels
    await session.flush()


async def _dim_def(ctx: Ctx, tid: str, rows: list[CrItem]) -> set[str]:
    d = await _dim(ctx, tid)
    values = {
        "kind": d.kind.value,
        "object": d.object.code if d.object is not None else "",
        "attr": d.attr,
        "column": d.source_column,
    }
    values.update({r.item_key: r.after for r in rows if r.op != "delete" and r.item_key in values})
    kind = _label(DimKind, values["kind"], "类型")
    obj = await _dimension_source(
        ctx.session, tid, kind.value, values["object"], attr=values["attr"], column=values["column"]
    )
    d.kind, d.object, d.object_name = kind, obj, obj.display_name if obj is not None else ""
    for r in rows:
        if r.item_key == "levels":
            await _set_dim_levels(ctx.session, d, [] if r.op == "delete" else r.after, ctx.by)
            continue
        if r.op == "delete":
            continue
        if r.item_key == "owner":
            d.owner = await owner_name(ctx, r.after)
        elif r.item_key in D_FIELDS:
            setattr(d, D_FIELDS[r.item_key], str(r.after or ""))
    return {"dimensions"}


register("dimension", "def")(_dim_def)
register("dimension", "config")(_dim_def)


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

    快照里没有的对象交由对象类型节标记，并在关系恢复后尝试删除；有引用时保留草稿。
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
        for n, (api, after) in enumerate(snap.items()):
            flags = snapshot_flags(o.code, after, n, require_ontology_id(session))
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
                    backing_column=flags["backing_column"],
                    seq=seq,
                    created_by="系统",
                )
                o.properties.append(p)
            _set_prop(
                p,
                {
                    **dict.fromkeys(P_FLAGS, False),
                    "enumName": None,
                    **after,
                    **{key: flags[col] for key, col in DISPLAY_FIELDS.items()},
                },
            )
    await session.flush()


async def restore_links(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """LinksSection.restore_section：快照里有的关系字段按快照回写；表里已发布但快照没有的置草稿（不再出现在快照节）。

    快照里有、表里没有的（物理删除过）不重建，记 warning。
    """
    ver = await _restore_ver(session, release_id)
    have = {x.code: x for x in await session.scalars(select(LinkType))}
    for code, d in content.items():
        lk = have.get(code)
        if lk is None:  # 物理删过：按快照重建
            lk = LinkType(
                code=code,
                display_name=d.get("name", code),
                from_code=d.get("from", ""),
                to_code=d.get("to", ""),
                cardinality=Cardinality.many_to_one,
                key_expr="",
                from_prop="",
                to_prop="",
                join_sql="",
                reverse_name="",
                nullable=False,
                description="",
                status=DefStatus.published,
                owner=d.get("owner", ""),
                usage=[],
                history=[],
                seq=max((x.seq for x in have.values()), default=0) + 1,
                created_by="系统",
            )
            session.add(lk)
            have[code] = lk
        lk.display_name, lk.api_name = d.get("name", lk.display_name), d.get("api")
        lk.from_code, lk.to_code = d.get("from", lk.from_code), d.get("to", lk.to_code)
        lk.cardinality = Cardinality(d.get("card", lk.cardinality.value))
        lk.key_expr, lk.join_sql = d.get("key", lk.key_expr), d.get("join", lk.join_sql)
        lk.from_prop, lk.to_prop, lk.via = d.get("fromProp", ""), d.get("toProp", ""), d.get("via")
        lk.reverse_name, lk.nullable = d.get("reverse", ""), bool(d.get("nullable"))
        lk.description, lk.owner = d.get("desc", ""), d.get("owner", lk.owner)
        lk.status, lk.since_release, lk.cr_code = DefStatus.published, d.get("since"), d.get("cr")
        lk.history = [{"ver": ver, "cr": "", "by": "系统", "change": "回滚恢复"}, *(d.get("history") or [])]
    objs = {o.code: o.id for o in await session.scalars(select(ObjectType))}
    for lk in have.values():
        lk.from_object_id, lk.to_object_id = objs.get(lk.from_code), objs.get(lk.to_code)
    for code, lk in have.items():
        if code not in content and lk.status == DefStatus.published:
            lk.status = DefStatus.draft  # 先置草稿；qaPaths 恢复完后 _restore_drop 再物理删
            _drops(session)["links"].add(code)
    await session.flush()


async def restore_dimensions(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """DimensionsSection.restore_section：回写维度定义字段、取值 / 说法 / 问数设置 / 描述。

    - 表里有、快照没有的维度（回滚掉的版本里新建的）物理删除（层级 / 取值 / 说法外键级联）。
    - 快照里有、表里没有的维度不重建，记 warning。

    - 取值：快照里有的按快照回写并清 until；表里在用、快照没有的置 until = 这次回滚的版本。
    - 说法：按快照覆盖（多的删、少的补）。
    """
    # Validate the entire replacement before deleting dimensions or changing any current definition.
    for code, snap in content.items():
        if isinstance(snap, dict) and "levels" in snap:
            _check_restored_value_levels(
                code, _dim_level_rows(code, snap["levels"]), snap.get("values") or []
            )
    dims = list(await session.scalars(select(Dimension)))
    sources = {}
    for d in dims:
        snap = content.get(d.code)
        if isinstance(snap, dict):
            kind = _label(DimKind, snap.get("kind", d.kind.value), "类型")
            obj = await _dimension_source(
                session,
                d.code,
                kind.value,
                snap.get("object", d.object.code if d.object is not None else ""),
                attr=snap.get("attr", d.attr),
                column=snap.get("column", d.source_column),
            )
            sources[d.code] = (kind, obj)
    ver = await _restore_ver(session, release_id)
    await _hard_delete(session, Dimension, [d for d in dims if d.code not in content])
    for code in set(content) - {d.code for d in dims}:
        log.warning("restore_dimensions: 维度 %s 表里不存在，跳过", code)
    for d in dims:
        snap = content.get(d.code)
        if not isinstance(snap, dict):
            continue
        d.kind, d.object = sources[d.code]
        d.object_name = d.object.display_name if d.object is not None else ""
        for k, col in D_FIELDS.items():
            if k in snap and k not in ("desc", "column", "valueSrc"):
                setattr(d, col, str(snap[k] or ""))
        d.source_column = str(snap.get("column", d.source_column) or "")
        d.value_src = str(snap.get("valueSrc", d.value_src) or "")
        if "status" in snap:
            d.status = DefStatus(snap["status"])
        if "history" in snap:
            d.history = list(snap["history"])
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
        if "levels" in snap:
            await _set_dim_levels(
                session, d, snap["levels"], "系统", replacement_values=snap.get("values") or []
            )
    await session.flush()


# ---------------------------------------------------------------- 整版回滚：objectTypes / metrics / qaPaths
O_SNAP = {**O_FIELDS}


def _drops(session: AsyncSession) -> dict[str, set[str]]:
    """回滚中待物理删的关系 / 对象（要等 qaPaths 重建、维度删完才没有外键引用）。"""
    return session.info.setdefault("static_restore_drops", {"links": set(), "objects": set()})


async def restore_object_types(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """ObjectTypesSection.restore_section：快照里有的对象按快照覆盖定义字段（表里没有的按快照重建）；
    表里有、快照没有的（回滚掉的版本里新建的）先置草稿，qaPaths 恢复后 _restore_drop 再尝试物理删。"""
    have = {o.code: o for o in await session.scalars(select(ObjectType))}
    seq = max((o.seq for o in have.values()), default=0)
    for code, d in content.items():
        o = have.get(code)
        if o is None:
            seq += 1
            o = ObjectType(
                code=code,
                api_name=d.get("api", code),
                display_name=d.get("name", code),
                obj_group=d.get("group", "其他"),
                description="",
                conflict_strategy=ConflictStrategy.user_edit_wins,
                lifecycle=Lifecycle.active,
                status=ObjectStatus.published,
                properties=[],
                seq=seq,
                created_by="系统",
            )
            session.add(o)
        for k, col in O_SNAP.items():
            if k in d:
                setattr(o, col, d[k])
            elif k in ("domain", "owner", "table", "source", "pk"):
                setattr(o, col, None)
        if d.get("conflict"):  # 快照是展示文本「策略（说明）」（shapes.conflict_text）
            base, _, note = str(d["conflict"]).partition("（")
            try:
                o.conflict_strategy = ConflictStrategy(base)
                o.conflict_note = note.removesuffix("）") or None
            except ValueError:
                log.warning(
                    "restore_object_types: %s 冲突策略「%s」不认识，保留表里的值", code, d["conflict"]
                )
        if "qs" in d:
            o.sample_questions = list(d["qs"] or [])
        if d.get("status"):
            o.status = ObjectStatus(d["status"])
    for code, o in have.items():
        if code not in content:
            o.status = ObjectStatus.draft
            _drops(session)["objects"].add(code)
    await session.flush()


async def restore_metrics(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """按快照恢复指标；快照外仍被已存定义或问数引用的指标保留为草稿。"""
    from app.contracts.qa import has_metric_references
    from app.modules.static.metric_versions import EXEC_KEYS, ledger, with_exec

    have = {m.code: m for m in await session.scalars(select(Metric))}
    unused = []
    for code, m in have.items():
        if code in content:
            continue
        used = bool(m.ref_rows) or any(
            v.get("code") == code
            for other in have.values()
            if other.code != code
            for v in other.variants or []
            if isinstance(v, dict)
        )
        used = used or bool(
            await session.scalar(
                select(KnowledgeApply.id)
                .where(KnowledgeApply.target_kind == ApplyKind.metric, KnowledgeApply.target_code == code)
                .limit(1)
            )
        )
        if used or await has_metric_references(session, code):
            m.status = MetricStatus.draft
            log.warning("restore_metrics: %s 仍被引用，保留为草稿", code)
        else:
            unused.append(m)
    await _hard_delete(session, Metric, unused)
    seq = max((m.seq for m in have.values()), default=0)
    for code, d in content.items():
        m = have.get(code)
        if m is None:
            seq += 1
            m = Metric(code=code, seq=seq, created_by="系统", api_name=code, display_name=code)
            m.metric_group, m.unit, m.version, m.owner = "其他", "", "v1", ""
            m.status = MetricStatus.published
            session.add(m)
        m.display_name, m.api_name = d.get("name", m.display_name), d.get("api", m.api_name)
        m.metric_group, m.unit = d.get("group", m.metric_group), d.get("unit", m.unit)
        m.version, m.owner = d.get("version", m.version), d.get("owner", m.owner)
        m.variants = list(d.get("variants") or [])
        if d.get("status"):
            m.status = MetricStatus(d["status"])
        review = d.get("review") or {}
        m.review_cr, m.review_note = review.get("cr"), review.get("note")
        if isinstance(d.get("execDef"), dict):
            # 带 execDef 的快照才知道当时的执行口径；旧快照不带，保持现值。
            rows = m.def_rows or []
            for key in EXEC_KEYS:
                value = d["execDef"].get(key)
                rows = with_exec(rows, key, value if isinstance(value, str) else None)
            m.def_rows = rows
        if "formula" in d or "dimNames" in d:
            m.formula, m.dims = d.get("formula"), list(d.get("dimNames") or [])
        else:
            m.formula, m.dims = None, None
        m.version_rows = await ledger(session, code, m)
    await session.flush()


async def restore_qa_paths(session: AsyncSession, content: dict[str, Any], *, release_id: int) -> None:
    """QaPathsSection.restore_section：问数路径三张表按快照重建（路径 / 步骤 / 试问），再 _restore_drop。

    引用的对象或关系表里没有时跳过该路径 / 组，记 warning。
    """
    await session.execute(delete(QaPathExample))
    await session.execute(delete(QaPathStep))
    await session.execute(delete(QaPath))
    objs = {o.code: o for o in await session.scalars(select(ObjectType))}
    links = {x.code: x for x in await session.scalars(select(LinkType))}
    for key, g in content.items():
        fo, to = objs.get(g.get("from")), objs.get(g.get("to"))
        if fo is None or to is None:
            log.warning("restore_qa_paths: 组 %s 的对象不存在，跳过", key)
            continue
        paths: list[QaPath | None] = []
        for i, p in enumerate(g.get("paths") or []):
            steps = [links.get(c) for c in p.get("links") or []]
            if any(x is None for x in steps):
                log.warning("restore_qa_paths: 组 %s 路径 %s 引用的关系不存在，跳过", key, i)
                paths.append(None)
                continue
            qp = QaPath(
                group_key=key,
                from_object=fo,
                to_object=to,
                seq=i,
                is_default=bool(p.get("isDefault")),
                hint=p.get("hint"),
                check_note=g.get("check") if p.get("isDefault") else None,
                steps=[QaPathStep(step_no=j, link=lk, created_by="系统") for j, lk in enumerate(steps)],
                created_by="系统",
            )
            session.add(qp)
            paths.append(qp)
        for i, e in enumerate(g.get("examples") or []):
            n = e.get("path", -1)
            session.add(
                QaPathExample(
                    from_object=fo,
                    to_object=to,
                    seq=i,
                    question=e.get("q", ""),
                    how=e.get("how"),
                    path=paths[n] if 0 <= n < len(paths) else None,
                    created_by="系统",
                )
            )
    await session.flush()
    await _restore_drop(session)


async def _restore_drop(session: AsyncSession) -> None:
    """物理删回滚掉的关系 / 对象。仍被引用（如别的模块的表）时保留为草稿，记 warning。"""
    drops = session.info.pop("static_restore_drops", None) or {}
    for model, key in ((LinkType, "links"), (ObjectType, "objects")):
        for code in sorted(drops.get(key, ())):
            inst = await session.scalar(select(model).where(model.code == code))
            if inst is None:
                continue
            try:
                async with session.begin_nested():
                    await _hard_delete(session, model, [inst])
            except Exception:
                log.warning("restore: %s %s 仍被引用，保留为草稿", key, code)


async def _hard_delete(session: AsyncSession, model: Any, rows: list[Any]) -> None:
    """按 id 直接 DELETE（子表靠外键级联），再把实例移出 session（免得之后 flush 碰到已删行）。"""
    if not rows:
        return
    await session.flush()
    await session.execute(delete(model).where(model.id.in_([r.id for r in rows])))
    for r in rows:
        session.expunge(r)
