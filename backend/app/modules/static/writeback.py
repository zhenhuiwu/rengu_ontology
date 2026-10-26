"""发布写回：整版发布时（同一事务、create_version 之前）把已通过变更单的 cr_item 写回定义表。

按 (target_kind, section) 登记写回函数（register）；没登记的段跳过、原样留在 cr_item 里，
发布事件 desc 里列出「未写回」。静态登记：knowledge/content、knowledge/status、object/properties。
动作 / 事件（Palantir）、规则 / 生命周期 lc_*（动态）由各模块自己 register，静态不碰它们的表。

写回后把改到的快照节名记进 session.info[DIRTY]，sections._Base.build_section 据此重组该节，
其它节照旧复用上一版快照。
"""

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
    ChangeRequest,
    CrItem,
    CrTarget,
    Knowledge,
    KnowledgeApply,
    KnowledgeStatus,
    KnowledgeType,
    ObjectType,
    Property,
)

DIRTY = "static_dirty_sections"


@dataclass
class Ctx:
    session: AsyncSession
    cr: ChangeRequest
    ver: str
    now: datetime
    by: str
    is_new: bool


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
            (t.target_kind, t.target_id): t.is_new
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
            ctx = Ctx(session, cr, ver, now, by, new.get((kind, tid), False))
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
    o = await ctx.session.scalar(select(ObjectType).where(ObjectType.code == tid))
    if o is None:
        # 新建对象类型的写回契约还没定（handoff），整段跳过会丢改动，所以直接拒绝发布
        raise _bad(f"对象类型 {tid} 不存在，新建对象类型暂不支持发布写回", kind="object", id=tid)
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
