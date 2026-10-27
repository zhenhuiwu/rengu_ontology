"""发布快照 `actionTypes` 段（action-event-api §4.2）。

形状：`{<动作 id>: {id,name,api,group,status,version,target,targetApi,targetKind,owner,desc,runners,approval,
sysId,endpoint,idemKey, params[], conds[], writeback{edits[],effects[]}, usedBy[]}}`。
system / timeout / retry / idempotent（随 ext_system）和统计字段不进快照。

- build_section：七段草稿写回后，改到的类型各追加一版完整定义，再导出当前未下线定义。
- seed_section：mock 只有当前定义，历史各版都用当前定义组装 [推断]（前端 ?ver= 显示的也是当前 mock）。
- restore_section(session, content, *, release_id)（整版回滚，团队约定的签名）：同一事务里把定义表
  改回 content；content 里没有的类型置 retired_release_id = release_id（必填：骨架 restore_sections
  先写新回滚记录、再把它的 id 传进来），
  content 里有的清掉 retired_release_id（重新发布即恢复）。版本表只追加，回滚不写版本行 [推断]。
- 登记：骨架的 load_sections() 发现本文件、调用幂等的 register()（create_app / seed CLI 各调一次）；
  import 本文件不登记。
"""

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import from_api
from app.modules.action.models import (
    ActionParam,
    ActionType,
    ActionTypeRule,
    ActionTypeVersion,
    CriterionKind,
    EditOp,
    EffectKind,
    OntologyEditRule,
    SideEffect,
    SubmissionCriterion,
    TypeStatus,
)
from app.modules.integration.models import ExtSystem

SECTION = "actionTypes"
GROUP = "action"


def action_def(at: ActionType) -> dict[str, Any]:
    return {
        "id": at.code,
        "name": at.name,
        "api": at.api,
        "group": at.grp,
        "status": at.status.value,
        "version": at.version,
        "target": at.target,
        "targetApi": at.target_api,
        "targetKind": at.target_kind,
        "owner": at.owner,
        "desc": at.desc_tpl,
        "runners": at.runners,
        "approval": at.approval,
        "sysId": at.system.code if at.system else None,
        "endpoint": at.endpoint,
        "idemKey": at.biz_key_template,
        "params": [
            {
                "name": p.name,
                "api": p.api,
                "type": p.type,
                "required": p.required,
                "def": p.default_text,
                "rule": p.rule_text,
            }
            for p in at.params
        ],
        "conds": [
            {"kind": c.kind.value, "name": c.name, "expr": c.expr_text, "fail": c.fail_msg} for c in at.conds
        ],
        "writeback": {
            "edits": [{"op": e.op.value, "obj": e.obj, "field": e.field} for e in at.edits],
            "effects": [
                {"kind": e.kind.value, "target": e.target, "when": e.when_text, "note": e.note}
                for e in at.effects
            ],
        },
        "usedBy": [r.rule_code for r in at.rules],
    }


def with_policy(definition: dict[str, Any], policy: dict[str, Any] | None) -> dict[str, Any]:
    """执行策略只在设置过时进定义 / 快照；没有 = 默认人工确认，存量定义形状不变。"""
    return {**definition, "policy": policy} if policy is not None else definition


async def _policies(
    session: AsyncSession, types: list[ActionType], changed: dict[str, Any]
) -> dict[str, Any]:
    """本次改到的取新值，其它沿用当前版本定义里的策略；自动执行要求免审（发布校验，409）。"""
    from app.modules.action.policy import version_policies
    from app.modules.static.writeback import writeback_error

    current = await version_policies(session, [(at.id, at.version) for at in types])
    result = {at.code: changed[at.code] if at.code in changed else current.get(at.id) for at in types}
    for at in types:
        if (result[at.code] or {}).get("mode") == "auto" and at.approval.strip() != "免审":
            raise writeback_error("自动执行的动作必须配置为免审", id=at.code, field="policy")
    return result


def _derived(ctx: Any, file: str, type_id: str) -> Any:
    for call in ctx.load(file, group=GROUP):
        a = call.get("args") or []
        if a and isinstance(a[0], dict) and a[0].get("id") == type_id:
            return call["result"]
    return None


def action_def_from_json(t: dict[str, Any], ctx: Any) -> dict[str, Any]:
    wb = _derived(ctx, "derived/writebackOf.json", t["id"]) or {}
    return {
        **{
            k: t.get(k)
            for k in (
                "id", "name", "api", "group", "status", "version", "target", "targetApi", "targetKind",
                "owner", "desc", "runners", "approval", "sysId", "endpoint", "idemKey",
            )
        },
        "params": _derived(ctx, "derived/paramsOf.json", t["id"]) or [],
        "conds": _derived(ctx, "derived/condsOf.json", t["id"]) or [],
        "writeback": {"edits": wb.get("edits", []), "effects": wb.get("effects", [])},
        "usedBy": list(t.get("usedBy") or []),
    }  # fmt: skip


async def current_types(session: AsyncSession) -> list[ActionType]:
    stmt = select(ActionType).where(ActionType.retired_release_id.is_(None)).order_by(ActionType.id)
    return list((await session.scalars(stmt.execution_options(populate_existing=True))).all())


class ActionTypesSection:
    section = SECTION

    async def build_section(
        self, session: AsyncSession, base: Any | None, change_order_ids: list[str]
    ) -> Any:
        from app.modules.action.writeback import PENDING, finish_versions

        types = await current_types(session)
        policies = await _policies(session, types, session.info.get(PENDING, {}).pop("policy", {}))
        await finish_versions(
            session, "action", types, change_order_ids, ActionTypeVersion, "action_type_id",
            lambda at: with_policy(action_def(at), policies.get(at.code)),
        )
        return {at.code: with_policy(action_def(at), policies.get(at.code)) for at in types}

    def seed_section(self, ver: str, ctx: Any) -> Any:
        return {t["id"]: action_def_from_json(t, ctx) for t in ctx.load("ACTION_TYPES.json", group=GROUP)}

    async def restore_section(self, session: AsyncSession, content: Any, *, release_id: int) -> None:
        if content is None:  # 那一版快照里没有这一节：什么都不做 [推断]
            return
        await restore_action_types(session, content, release_id)


async def retire_missing(session: AsyncSession, model: Any, keep: set[str], release_id: int) -> None:
    """content 里没有的类型下线（retired_release_id = 新回滚记录）；已下线的保持原来的下线版本。"""
    await session.execute(
        update(model)
        .where(model.code.not_in(keep) if keep else True, model.retired_release_id.is_(None))
        .values(retired_release_id=release_id)
        .execution_options(synchronize_session=False)
    )


def _children(at: ActionType, d: dict[str, Any]) -> list[Any]:
    """按快照重建子表行。子行 is_seed 跟随父行（mock 自带重复 api 的参数只在 seed 行上放行）[推断]。"""
    seed = at.is_seed
    rows: list[Any] = [
        ActionParam(
            action_type_id=at.id, seq=i, name=p["name"], api=p["api"], type=p["type"],
            required=bool(p.get("required")), default_text=p.get("def") or "", rule_text=p.get("rule") or "",
            is_seed=seed,
        )
        for i, p in enumerate(d.get("params") or [], 1)
    ]  # fmt: skip
    rows += [
        SubmissionCriterion(
            action_type_id=at.id, seq=i, kind=from_api(CriterionKind, c["kind"]), name=c["name"],
            expr_text=c.get("expr") or "", fail_msg=c.get("fail") or "", is_seed=seed,
        )
        for i, c in enumerate(d.get("conds") or [], 1)
    ]  # fmt: skip
    wb = d.get("writeback") or {}
    rows += [
        OntologyEditRule(
            action_type_id=at.id, seq=i, op=from_api(EditOp, e["op"]), obj=e["obj"],
            field=e.get("field") or "", is_seed=seed,
        )
        for i, e in enumerate(wb.get("edits") or [], 1)
    ]  # fmt: skip
    rows += [
        SideEffect(
            action_type_id=at.id, seq=i, kind=from_api(EffectKind, e["kind"]), target=e["target"],
            when_text=e.get("when") or "", note=e.get("note") or "", is_seed=seed,
        )
        for i, e in enumerate(wb.get("effects") or [], 1)
    ]  # fmt: skip
    rows += [
        ActionTypeRule(action_type_id=at.id, rule_code=r, seq=i, is_seed=seed)
        for i, r in enumerate(d.get("usedBy") or [], 1)
    ]
    return rows


async def restore_action_types(session: AsyncSession, content: dict[str, Any], release_id: int) -> None:
    systems = {s.code: s.id for s in (await session.scalars(select(ExtSystem))).all()}
    existing = {at.code: at for at in (await session.scalars(select(ActionType))).all()}
    old_rules = {
        (r.action_type_id, r.rule_code): r.generates
        for r in (await session.scalars(select(ActionTypeRule))).all()
    }
    for code, d in content.items():
        at = existing.get(code) or ActionType(code=code)
        at.name, at.api, at.grp = d["name"], d["api"], d.get("group") or ""
        at.status, at.version = from_api(TypeStatus, d["status"]), d["version"]
        at.target, at.target_api, at.target_kind = (
            d.get("target") or "",
            d.get("targetApi") or "",
            d.get("targetKind"),
        )
        at.owner, at.desc_tpl = d.get("owner") or "", d.get("desc") or ""
        at.runners, at.approval = d.get("runners") or "", d.get("approval") or ""
        at.sys_id = systems.get(d["sysId"]) if d.get("sysId") else None
        at.endpoint, at.biz_key_template = d.get("endpoint") or "—", d.get("idemKey") or ""
        at.retired_release_id = None
        session.add(at)
        await session.flush()
        for m in (ActionParam, SubmissionCriterion, OntologyEditRule, SideEffect, ActionTypeRule):
            await session.execute(delete(m).where(m.action_type_id == at.id))
        rows = _children(at, d)
        for r in rows:
            if isinstance(r, ActionTypeRule):
                r.generates = old_rules.get(
                    (at.id, r.rule_code), False
                )  # generates 来自 RULE_ACTION，不进快照
        session.add_all(rows)
        await session.flush()
        await session.refresh(at, ["params", "conds", "edits", "effects", "rules", "system"])
    await retire_missing(session, ActionType, set(content), release_id)


BUILDER = ActionTypesSection()


def register() -> ActionTypesSection:
    """幂等：已登记（本实例或测试替身）就不重复登记。由骨架 load_sections() 调用，import 时不登记。"""
    from app.modules.action.writeback import register as register_writebacks
    from app.modules.versioning.sections import register_section, registered_sections

    register_writebacks()
    if SECTION not in registered_sections():
        register_section(BUILDER)
    return BUILDER
