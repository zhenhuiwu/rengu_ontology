"""发布快照 `rules` / `lifecycles` 两段（dynamic-api.md §5；CONVENTIONS §7）。

形状（段内按前端 id 作键，和 mock 一致）：
- rules：`{<R-xxx>: RuleDef}`（rules.ts RuleDef：id,name,version,status,owner,core,objKind,level,output,
  weekly,
  desc,conds,logic,expr,dedupe,cooldown,trigger,actionTypeId,autoStage）；
- lifecycles：`{<objectId>: Lifecycle}`（lifecycle.ts，去掉 versions 历史：objectId,objectName,field,version,
  owner,updatedAt,ruleId?,desc,states[],transitions[]）。

- build_section：发布时整段导出当前定义（version 为 null 的 = 回滚后不在快照里，不导出）；
  master 还没有变更单表，草稿不合入 [推断]。
- seed_section：mock 只有当前定义，历史各版都用当前定义组装 [推断]（同 Palantir action/sections.py）。
- restore_section(session, content, *, release_id)：整版回滚时把 rule / lifecycle 当前定义改回 content；
  content 里没有的规则 / 生命周期 version 置 null（不再出现在快照，接口照常可读）[推断]。
  生命周期按 (objectId, version) 找版本行，状态 / 迁移不在就按 content 补建；版本表只追加。
- 登记：只导出 BUILDERS，由骨架 load_sections() 登记；import 本文件不登记。
"""

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schema import parse_display_dt
from app.modules.action.models import ActionType
from app.modules.dynamic.models import AlertLevel
from app.modules.dynamic.rules_models import (
    LcStateKind,
    LcTrigger,
    Lifecycle,
    LifecycleState,
    LifecycleTransition,
    LifecycleVersion,
    Rule,
    RuleOutput,
    RuleStatus,
)
from app.modules.event.models import EventType

GROUP = "dynamic"


def _fmt_min(dt: Any) -> str:
    from app.core.schema import _fmt_dt

    return _fmt_dt(dt)


def rule_def(r: Rule, action_code: str | None) -> dict[str, Any]:
    return {
        "id": r.code,
        "name": r.name,
        "version": r.version,
        "status": r.status.value,
        "owner": r.owner,
        "core": r.core,
        "objKind": r.obj_kind,
        "level": r.level.value,
        "output": r.output.value,
        "weekly": r.weekly,
        "desc": r.desc,
        "conds": r.conds,
        "logic": r.logic,
        "expr": r.expr,
        "dedupe": r.dedupe,
        "cooldown": r.cooldown,
        "trigger": r.trigger,
        "actionTypeId": action_code,
        "autoStage": r.auto_stage,
    }


def state_def(s: LifecycleState) -> dict[str, Any]:
    d: dict[str, Any] = {
        "key": s.key,
        "name": s.name,
        "kind": s.kind.value,
        "desc": s.desc,
        "count": s.count,
        "x": s.x,
        "y": s.y,
    }
    if s.sla is not None:
        d["sla"] = s.sla
    return d


def transition_def(
    t: LifecycleTransition, keys: dict[int, str], events: dict[int, str], actions: dict[int, str]
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": t.tr_code,
        "from": keys[t.from_state_id],
        "to": keys[t.to_state_id],
        "trigger": t.trigger.value,
        "by": t.by,
        "guard": t.guard,
        "effect": t.effect,
        "weekly": t.weekly,
    }
    if t.event_type_id is not None:
        d["eventId"] = events[t.event_type_id]
    if t.action_type_id is not None:
        d["actionId"] = actions[t.action_type_id]
    return d


async def code_map(session: AsyncSession, model: Any) -> dict[int, str]:
    return {i: c for i, c in (await session.execute(select(model.id, model.code))).all()}


async def lifecycle_machine(session: AsyncSession, version_id: int) -> dict[str, Any]:
    states = list(
        (
            await session.scalars(
                select(LifecycleState)
                .where(LifecycleState.version_id == version_id)
                .order_by(LifecycleState.seq)
            )
        ).all()
    )
    trs = list(
        (
            await session.scalars(
                select(LifecycleTransition)
                .where(LifecycleTransition.version_id == version_id)
                .order_by(LifecycleTransition.seq)
            )
        ).all()
    )
    keys = {s.id: s.key for s in states}
    events = await code_map(session, EventType)
    actions = await code_map(session, ActionType)
    return {
        "states": [state_def(s) for s in states],
        "transitions": [transition_def(t, keys, events, actions) for t in trs],
    }


async def version_row(session: AsyncSession, lc: Lifecycle, ver: int | None) -> LifecycleVersion | None:
    if ver is None:
        return None
    return await session.scalar(
        select(LifecycleVersion).where(
            LifecycleVersion.lifecycle_id == lc.id, LifecycleVersion.version == ver
        )
    )


def lifecycle_head(lc: Lifecycle) -> dict[str, Any]:
    d: dict[str, Any] = {
        "objectId": lc.code,
        "objectName": lc.object_name,
        "field": lc.field,
        "version": lc.version,
        "owner": lc.owner,
        "updatedAt": _fmt_min(lc.updated_on),
    }
    if lc.rule_code:
        d["ruleId"] = lc.rule_code
    d["desc"] = lc.desc
    return d


async def lifecycle_def(session: AsyncSession, lc: Lifecycle) -> dict[str, Any]:
    v = await version_row(session, lc, lc.version)
    machine = await lifecycle_machine(session, v.id) if v else {"states": [], "transitions": []}
    if v and not machine["states"] and v.snapshot:
        machine = v.snapshot
    return {**lifecycle_head(lc), **machine}


# ---------------- rules ----------------


class RulesSection:
    section = "rules"

    async def build_section(
        self, session: AsyncSession, base: Any | None, change_order_ids: list[str]
    ) -> Any:
        actions = await code_map(session, ActionType)
        rows = (await session.scalars(select(Rule).where(Rule.version.is_not(None)).order_by(Rule.id))).all()
        return {r.code: rule_def(r, actions.get(r.action_type_id)) for r in rows}

    def seed_section(self, ver: str, ctx: Any) -> Any:
        # 可选键补齐成 null，和 build_section 的形状一致（快照可比较）
        return {
            r["id"]: {**r, "actionTypeId": r.get("actionTypeId"), "autoStage": r.get("autoStage")}
            for r in ctx.load("RULE_DEFS.json", group=GROUP)
        }

    async def restore_section(self, session: AsyncSession, content: Any, *, release_id: int) -> None:
        if content is None:
            return
        actions = {c: i for i, c in (await code_map(session, ActionType)).items()}
        rows = {r.code: r for r in (await session.scalars(select(Rule))).all()}
        for code, d in content.items():
            r = rows.get(code)
            if r is None:
                r = Rule(code=code, is_seed=False)
                session.add(r)
            r.name, r.version, r.owner = d["name"], d.get("version"), d["owner"]
            r.status = RuleStatus(d["status"])
            r.core = bool(d.get("core"))
            r.obj_kind, r.level, r.output = d["objKind"], AlertLevel(d["level"]), RuleOutput(d["output"])
            r.weekly, r.desc, r.conds, r.logic, r.expr = (
                d.get("weekly", 0),
                d["desc"],
                d["conds"],
                d["logic"],
                d["expr"],
            )
            r.dedupe, r.cooldown, r.trigger, r.auto_stage = (
                d["dedupe"],
                d["cooldown"],
                d["trigger"],
                d["autoStage"],
            )
            r.action_type_id = actions.get(d.get("actionTypeId")) if d.get("actionTypeId") else None
            r.release_id = release_id
        missing = set(rows) - set(content)
        if missing:
            await session.execute(
                update(Rule)
                .where(Rule.code.in_(missing))
                .values(version=None, release_id=release_id)
                .execution_options(synchronize_session=False)
            )
        await session.flush()


# ---------------- lifecycles ----------------


class LifecyclesSection:
    section = "lifecycles"

    async def build_section(
        self, session: AsyncSession, base: Any | None, change_order_ids: list[str]
    ) -> Any:
        rows = (
            await session.scalars(
                select(Lifecycle).where(Lifecycle.version.is_not(None)).order_by(Lifecycle.id)
            )
        ).all()
        return {lc.code: await lifecycle_def(session, lc) for lc in rows}

    def seed_section(self, ver: str, ctx: Any) -> Any:
        """该版发布时生效的生命周期 = lifecycleAsOf(objectId, 发布时间)（lifecycle.ts L280）：
        取发布时间之前最近的生命周期版本 v，
        用 derived/lifecycleAsOf(objectId, v.at)（mock 导出的就是各版本时间点）。"""
        rel_at = {r["ver"]: r["publishedAt"] for r in ctx.load("RELEASES.json", group="static")}.get(ver)
        as_of = {tuple(x["args"]): x["result"] for x in ctx.load("derived/lifecycleAsOf.json", group=GROUP)}
        out = {}
        for x in ctx.load("LIFECYCLES.json", group=GROUP):
            eff = next((v for v in x["versions"] if rel_at and v["at"] <= rel_at), None)
            src = as_of.get((x["objectId"], eff["at"])) if eff and eff["ver"] != x["version"] else x
            out[x["objectId"]] = {k: v for k, v in (src or x).items() if k != "versions"}
        return out

    async def restore_section(self, session: AsyncSession, content: Any, *, release_id: int) -> None:
        if content is None:
            return
        rows = {lc.code: lc for lc in (await session.scalars(select(Lifecycle))).all()}
        for code, d in content.items():
            lc = rows.get(code)
            if lc is None:  # 快照里有、表里没有（被删）：本批没有删除生命周期的接口，跳过 [推断]
                continue
            lc.version = d.get("version")
            lc.object_name, lc.field, lc.owner, lc.desc = (
                d["objectName"],
                d["field"],
                d["owner"],
                d.get("desc", ""),
            )
            lc.updated_on = parse_display_dt(d["updatedAt"])
            lc.rule_code = d.get("ruleId")
            await session.flush()
            await ensure_machine(session, lc, d, release_id=release_id)
        missing = set(rows) - set(content)
        if missing:
            await session.execute(
                update(Lifecycle)
                .where(Lifecycle.code.in_(missing))
                .values(version=None)
                .execution_options(synchronize_session=False)
            )
        await session.flush()


async def ensure_machine(
    session: AsyncSession,
    lc: Lifecycle,
    d: dict[str, Any],
    *,
    release_id: int | None,
    by: str = "系统（回滚）",
) -> LifecycleVersion:
    """确保 (lc, d.version) 的版本行和状态 / 迁移行存在且等于 d；版本行只追加，不改。"""
    v = await version_row(session, lc, d["version"])
    if v is None:
        from app.core.clock import now

        v = LifecycleVersion(
            code=f"{lc.code}:v{d['version']}",
            lifecycle_id=lc.id,
            version=d["version"],
            at=now(),
            by=by,
            change="整版回滚恢复",
            release_id=release_id,
            snapshot={"states": d["states"], "transitions": d["transitions"]},
            is_seed=False,
        )
        session.add(v)
        await session.flush()
    states = {
        s.key: s
        for s in (
            await session.scalars(select(LifecycleState).where(LifecycleState.version_id == v.id))
        ).all()
    }
    for i, s in enumerate(d["states"], 1):
        row = states.get(s["key"])
        if row is None:
            row = LifecycleState(code=f"{lc.code}:v{d['version']}:{s['key']}", version_id=v.id, key=s["key"])
            session.add(row)
            states[s["key"]] = row
        row.seq, row.name, row.kind, row.desc = i, s["name"], LcStateKind(s["kind"]), s.get("desc", "")
        row.sla, row.count, row.x, row.y = s.get("sla"), s.get("count", 0), s["x"], s["y"]
    await session.flush()
    events = {c: i for i, c in (await code_map(session, EventType)).items()}
    actions = {c: i for i, c in (await code_map(session, ActionType)).items()}
    trs = {
        t.tr_code: t
        for t in (
            await session.scalars(select(LifecycleTransition).where(LifecycleTransition.version_id == v.id))
        ).all()
    }
    for i, t in enumerate(d["transitions"], 1):
        row = trs.get(t["id"])
        if row is None:
            row = LifecycleTransition(
                code=f"{lc.code}:v{d['version']}:{t['id']}", version_id=v.id, tr_code=t["id"]
            )
            session.add(row)
        row.seq, row.from_state_id, row.to_state_id = i, states[t["from"]].id, states[t["to"]].id
        row.trigger, row.by = LcTrigger(t["trigger"]), t["by"]
        row.event_type_id = events.get(t["eventId"]) if t.get("eventId") else None
        row.action_type_id = actions.get(t["actionId"]) if t.get("actionId") else None
        row.guard, row.effect, row.weekly = t.get("guard", ""), t.get("effect", ""), t.get("weekly", 0)
    await session.flush()
    return v


BUILDERS = [RulesSection(), LifecyclesSection()]
