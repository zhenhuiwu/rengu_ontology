"""发布快照 `actionTypes` 段（action-event-api §4.2）。

形状：`{<动作 id>: {id,name,api,group,status,version,target,targetApi,targetKind,owner,desc,runners,approval,
sysId,endpoint,idemKey, params[], conds[], writeback{edits[],effects[]}, usedBy[]}}`。
system / timeout / retry / idempotent（随 ext_system）和统计字段不进快照。

- build_section：发布时把当前定义表（未下线的）整段导出；本批没有草稿写入，不看变更单 [推断]。
- seed_section：mock 只有当前定义，历史各版都用当前定义组装 [推断]（前端 ?ver= 显示的也是当前 mock）。
- restore_section（整版回滚恢复定义表）本批未实现。
- **不在 import 时登记**：骨架测试（test_versioning / test_seed_fresh_db）假定没有 section 登记；
  启动时登记要和本体小白一起改测试，现在由 `register()` 显式调用。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.action.models import ActionType

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
    return list((await session.scalars(stmt)).all())


class ActionTypesSection:
    section = SECTION

    async def build_section(
        self, session: AsyncSession, base: Any | None, change_order_ids: list[str]
    ) -> Any:
        return {at.code: action_def(at) for at in await current_types(session)}

    def seed_section(self, ver: str, ctx: Any) -> Any:
        return {t["id"]: action_def_from_json(t, ctx) for t in ctx.load("ACTION_TYPES.json", group=GROUP)}


def register() -> ActionTypesSection:
    from app.modules.versioning.sections import register_section, registered_sections

    existing = registered_sections().get(SECTION)
    if isinstance(existing, ActionTypesSection):
        return existing
    b = ActionTypesSection()
    register_section(b)
    return b
