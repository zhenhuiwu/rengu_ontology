"""动态的发布写回：整版发布（同一事务、create_version 之前）把已通过变更单里的规则 / 生命周期草稿转正。

- 规则：新规则 → v1 已发布；已发布规则 → version+1，追加 rule_version；状态还原
  （变更中 → 已发布，已停用保持已停用）；删 rule_draft。
- 生命周期：按草稿 edits（addStates / addTransitions / editTransitions / removeTransitions）
  生成下一版 lifecycle_version + 状态 / 迁移行，lifecycle.version 指向新版；删 lifecycle_draft。
- 幂等：草稿已转正（本单没有草稿行）就什么都不做，重复调用 / 同一目标多段各调一次都安全。

静态 r4 的钩子（app.modules.static.writeback.register_writeback）存在时，本模块 import 即登记
(rule, conds|mapping|config) 与 (object, lc_states|lc_transitions)；不存在（master 上）则只暴露 apply_cr。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now as _now
from app.core.errors import AppError
from app.modules.admin.models import SysUser
from app.modules.dynamic.rules_models import (
    LcStateKind,
    LcTrigger,
    Lifecycle,
    LifecycleDraft,
    LifecycleVersion,
    Rule,
    RuleDraft,
    RuleStatus,
    RuleVersion,
)
from app.modules.dynamic.sections import ensure_machine, lifecycle_def

RULE_SECTIONS = ("conds", "mapping", "config")
LC_SECTIONS = ("lc_states", "lc_transitions")

# 草稿 patch 里可能出现的字段 → Rule 列（rule_def 的反向）
_RULE_FIELDS = {
    "name": "name",
    "desc": "desc",
    "objKind": "obj_kind",
    "conds": "conds",
    "logic": "logic",
    "expr": "expr",
    "dedupe": "dedupe",
    "cooldown": "cooldown",
    "trigger": "trigger",
    "autoStage": "auto_stage",
}


async def _who(session: AsyncSession, by: str) -> str:
    """发布人：传用户 code（U010001）时换成姓名，其它原样。"""
    return await session.scalar(select(SysUser.name).where(SysUser.code == by)) or by


async def promote_rule(
    session: AsyncSession, cr_code: str, rule_code: str, *, by: str, at: datetime | None = None
) -> bool:
    """把 cr_code 单里 rule_code 的草稿转正；没有该单的草稿返回 False（幂等）。"""
    r = await session.scalar(select(Rule).where(Rule.code == rule_code))
    if r is None:
        return False
    d = await session.scalar(select(RuleDraft).where(RuleDraft.rule_id == r.id, RuleDraft.cr_code == cr_code))
    if d is None:
        return False
    at, who = at or _now(), await _who(session, by)
    for k, col in _RULE_FIELDS.items():
        if k in d.patch:
            setattr(r, col, d.patch[k])
    ver = (r.version or 0) + 1
    r.version = ver
    if r.status != RuleStatus.disabled:
        r.status = RuleStatus.published
    r.published_by, r.published_at = who, at
    r.change_note = d.change or f"变更单 {cr_code} 发布"
    session.add(RuleVersion(rule_id=r.id, version=ver, by=who, at=at, change=r.change_note))
    await session.delete(d)
    await session.flush()
    return True


def apply_edits(machine: dict[str, Any], edits: dict[str, Any]) -> dict[str, Any]:
    """已发布状态机 + 草稿 edits → 新状态机（与页面 ObjBehaviorLifecycle 的叠加规则一致）。"""
    rm = set(edits.get("removeTransitions") or [])
    ed = edits.get("editTransitions") or {}
    states = [dict(s) for s in machine["states"]] + [dict(s) for s in edits.get("addStates") or []]
    trs = [{**t, **ed.get(t["id"], {})} for t in machine["transitions"] if t["id"] not in rm]
    trs += [dict(t) for t in edits.get("addTransitions") or []]
    for s in states:
        s.setdefault("desc", "")
        s.setdefault("count", 0)
        s.setdefault("x", 0)
        s.setdefault("y", 0)
    kinds, trigs = {k.value for k in LcStateKind}, {t.value for t in LcTrigger}
    keys = {s["key"] for s in states}
    for s in states:
        if s.get("kind") not in kinds:
            raise _bad(f"状态“{s['key']}”的类型“{s.get('kind')}”不认识", key=f"state.{s['key']}")
    for t in trs:
        if t.get("trigger") not in trigs or t.get("from") not in keys or t.get("to") not in keys:
            raise _bad(f"迁移 {t.get('id')} 的触发方式或起止状态无效", key=f"tr.{t.get('id')}")
    return {"states": states, "transitions": trs}


def _bad(msg: str, **details: Any) -> AppError:
    """与静态 writeback_error 同码：409 WRITEBACK_FAILED，整个发布回滚。"""
    return AppError(msg, code="WRITEBACK_FAILED", status_code=409, details={"kind": "object", **details})


async def promote_lifecycle(
    session: AsyncSession, cr_code: str, lc_code: str, *, by: str, at: datetime | None = None
) -> bool:
    """把 cr_code 单里 lc_code（= 对象类型 code）的生命周期草稿转正；没有草稿返回 False（幂等）。"""
    lc = await session.scalar(select(Lifecycle).where(Lifecycle.code == lc_code))
    if lc is None:
        return False
    d = await session.scalar(
        select(LifecycleDraft).where(LifecycleDraft.lifecycle_id == lc.id, LifecycleDraft.cr_code == cr_code)
    )
    if d is None:
        return False
    at, who = at or _now(), await _who(session, by)
    cur = await lifecycle_def(session, lc)
    m = apply_edits({"states": cur["states"], "transitions": cur["transitions"]}, d.edits or {})
    ver = (lc.version or 0) + 1
    session.add(
        LifecycleVersion(
            code=f"{lc.code}:v{ver}",
            lifecycle_id=lc.id,
            version=ver,
            at=at,
            by=who,
            change=f"变更单 {cr_code} 发布",
            snapshot=m,
            is_seed=False,
        )
    )
    await session.flush()
    await ensure_machine(session, lc, {"version": ver, **m}, release_id=None, by=who)
    lc.version, lc.updated_on = ver, at
    await session.delete(d)
    await session.flush()
    return True


async def apply_cr(
    session: AsyncSession, cr_code: str, *, by: str, at: datetime | None = None
) -> dict[str, list[str]]:
    """转正 cr_code 单里的全部规则 / 生命周期草稿（同一事务，不 commit）。返回实际转正的 code。"""
    rules = (
        await session.scalars(
            select(Rule.code)
            .join(RuleDraft, RuleDraft.rule_id == Rule.id)
            .where(RuleDraft.cr_code == cr_code)
        )
    ).all()
    lcs = (
        await session.scalars(
            select(Lifecycle.code)
            .join(LifecycleDraft, LifecycleDraft.lifecycle_id == Lifecycle.id)
            .where(LifecycleDraft.cr_code == cr_code)
        )
    ).all()
    out: dict[str, list[str]] = {"rules": [], "lifecycles": []}
    for c in rules:
        if await promote_rule(session, cr_code, c, by=by, at=at):
            out["rules"].append(c)
    for c in lcs:
        if await promote_lifecycle(session, cr_code, c, by=by, at=at):
            out["lifecycles"].append(c)
    return out


# ---------------------------------------------------------------- 挂静态发布钩子
# 钩子签名（static r4）：async (ctx: Ctx, target_id: str, rows: list[CrItem]) -> set[str]
# ctx.session / ctx.cr.code / ctx.by / ctx.now；返回改到的「静态」快照节名 → 动态返回空集合。


async def rule_hook(ctx: Any, tid: str, rows: list[Any]) -> set[str]:
    await promote_rule(ctx.session, ctx.cr.code, tid, by=ctx.by, at=ctx.now)
    return set()


async def lifecycle_hook(ctx: Any, tid: str, rows: list[Any]) -> set[str]:
    await promote_lifecycle(ctx.session, ctx.cr.code, tid, by=ctx.by, at=ctx.now)
    return set()


def register_hooks() -> bool:
    """有静态写回钩子就登记（幂等）；没有返回 False。"""
    try:
        from app.modules.static.writeback import register_writeback
    except ImportError:
        return False
    for s in RULE_SECTIONS:
        register_writeback("rule", s, rule_hook)
    for s in LC_SECTIONS:
        register_writeback("object", s, lifecycle_hook)
    return True


HOOKED = register_hooks()
