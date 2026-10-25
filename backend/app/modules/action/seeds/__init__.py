"""action 模块 seed（JSON 组 action；PDE 的 npm run export-mock 生成，内容不改）。

- ACTION_TYPES.json → action_type：code = id；sysId → ext_system.id；desc 存导出值（已做 {sys} 替换，见下）。
- ACTION_TYPES.json · usedBy → action_type_rule：rule_code 软引用；generates = RULE_ACTION[rule] == 本动作。
- derived/paramsOf.json → action_param：键 (action_type_id, seq)，
  mock 有重复 api（claim_alert / reassign_alert 的 alert）。
- derived/condsOf.json → submission_criterion：键 (action_type_id, name)。
- derived/writebackOf.json → ontology_edit_rule（键 action_type_id, seq）；
  side_effect（键 action_type_id, target）。
- derived/versionsOf.action.json → action_type_version：调用参数是 (version, owner, notes)，不带动作 id，
  第 i 次调用 = ACTION_TYPES[i]（导出顺序，校验 version / owner 一致）。

- desc：前端 DEFS 里的 `{sys}` 模板原文没有导出（导出的是 withSystem 之后的 ACTION_TYPES），
  desc_tpl 只能存替换后的文本 → 读接口再做一次 `{sys}` 替换是空操作。要原文需 PDE 在 export-mock 加导出项。
"""

from app.core.enums import from_api
from app.core.schema import parse_display_dt
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
from app.seeds import ARGS_KEY, Ref, Row, SeedContext, SeedSpec, derived_args

JSON_MODULE = "action"

PARENT = {"action_type_id": Ref(ActionType, "code", nullable=False)}


def _clean(r: Row) -> Row:
    return {k: v for k, v in r.items() if k != ARGS_KEY}


def derived_result(ctx: SeedContext, file: str, type_id: str) -> object:
    """派生文件里参数（第一个引用参数）= type_id 的那次调用的结果。"""
    for call in ctx.load(file):
        a = call.get("args") or []
        if a and isinstance(a[0], dict) and a[0].get("id") == type_id:
            return call["result"]
    raise ValueError(f"{file} 里没有 {type_id} 的调用")


def _seq(ctx: SeedContext, file: str, r: Row, sub: str | None = None) -> int:
    res = derived_result(ctx, file, derived_args(r)[0])
    items = res[sub] if sub else res  # type: ignore[index]
    return items.index(_clean(r)) + 1  # type: ignore[union-attr]


def _type(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "api": r["api"],
        "grp": r["group"],
        "status": from_api(TypeStatus, r["status"]).name,
        "version": r["version"],
        "target": r["target"],
        "target_api": r["targetApi"],
        "target_kind": r.get("targetKind"),
        "owner": r["owner"],
        "desc_tpl": r["desc"],
        "runners": r["runners"],
        "approval": r["approval"],
        "sys_id": r.get("sysId"),
        "endpoint": r["endpoint"],
        "biz_key_template": r["idemKey"],
    }


def _rules(r: Row, ctx: SeedContext) -> list[Row]:
    rule_action = ctx.load("RULE_ACTION.json")
    return [
        {"action_type_id": r["id"], "rule_code": rid, "seq": i, "generates": rule_action.get(rid) == r["id"]}
        for i, rid in enumerate(r.get("usedBy") or [], 1)
    ]


def _param(r: Row, ctx: SeedContext) -> Row:
    return {
        "action_type_id": derived_args(r)[0],
        "seq": _seq(ctx, "derived/paramsOf.json", r),
        "name": r["name"],
        "api": r["api"],
        "type": r["type"],
        "required": r["required"],
        "default_text": r["def"],
        "rule_text": r["rule"],
    }


def _cond(r: Row, ctx: SeedContext) -> Row:
    return {
        "action_type_id": derived_args(r)[0],
        "seq": _seq(ctx, "derived/condsOf.json", r),
        "kind": from_api(CriterionKind, r["kind"]).name,
        "name": r["name"],
        "expr_text": r["expr"],
        "fail_msg": r["fail"],
    }


def _edits(r: Row, _ctx: SeedContext) -> list[Row]:
    tid = derived_args(r)[0]
    return [
        {
            "action_type_id": tid,
            "seq": i,
            "op": from_api(EditOp, e["op"]).name,
            "obj": e["obj"],
            "field": e["field"],
        }
        for i, e in enumerate(r.get("edits") or [], 1)
    ]


def _effects(r: Row, _ctx: SeedContext) -> list[Row]:
    tid = derived_args(r)[0]
    return [
        {
            "action_type_id": tid,
            "seq": i,
            "kind": from_api(EffectKind, e["kind"]).name,
            "target": e["target"],
            "when_text": e["when"],
            "note": e["note"],
        }
        for i, e in enumerate(r.get("effects") or [], 1)
    ]


def versions_rows(r: Row, ctx: SeedContext, *, types_file: str, versions_file: str, fk: str) -> list[Row]:
    """versionsOf(version, owner, notes) 不带类型 id：第 i 次调用 = 第 i 个类型（校验 version / owner）。"""
    types = ctx.load(types_file)
    i = next(n for n, t in enumerate(types) if t["id"] == r["id"])
    call = ctx.load(versions_file)[i]
    if call["args"][0] != r["version"] or call["args"][1] != r["owner"]:
        raise ValueError(f"{versions_file} 第 {i} 次调用的参数和 {r['id']} 对不上：{call['args'][:2]}")
    return [
        {
            fk: r["id"],
            "ver": v["ver"],
            "release_ver": v["release"],
            "change_note": v["change"],
            "by_name": v["by"],
            "at": parse_display_dt(v["at"]),
        }
        for v in call["result"]
    ]


def _versions(r: Row, ctx: SeedContext) -> list[Row]:
    return versions_rows(
        r,
        ctx,
        types_file="ACTION_TYPES.json",
        versions_file="derived/versionsOf.action.json",
        fk="action_type_id",
    )


SEEDS: list[SeedSpec] = [
    SeedSpec(
        ActionType,
        "ACTION_TYPES.json",
        key=("code",),
        transform=_type,
        description="动作类型",
        refs={"sys_id": Ref(ExtSystem, "code")},
    ),
    SeedSpec(
        ActionTypeRule,
        "ACTION_TYPES.json",
        key=("action_type_id", "rule_code"),
        transform=_rules,
        description="动作类型 ↔ 规则（usedBy）",
        refs=PARENT,
    ),
    SeedSpec(
        ActionParam, "derived/paramsOf.json", key=("action_type_id", "seq"), transform=_param, refs=PARENT
    ),
    SeedSpec(
        SubmissionCriterion,
        "derived/condsOf.json",
        key=("action_type_id", "name"),
        transform=_cond,
        refs=PARENT,
    ),
    SeedSpec(
        OntologyEditRule,
        "derived/writebackOf.json",
        key=("action_type_id", "seq"),
        transform=_edits,
        refs=PARENT,
    ),
    SeedSpec(
        SideEffect,
        "derived/writebackOf.json",
        key=("action_type_id", "target"),
        transform=_effects,
        refs=PARENT,
    ),
    SeedSpec(
        ActionTypeVersion,
        "ACTION_TYPES.json",
        key=("action_type_id", "ver"),
        transform=_versions,
        description="动作类型版本（versionsOf）",
        refs=PARENT,
    ),
]
