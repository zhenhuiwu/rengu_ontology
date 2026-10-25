"""dynamic 第二部分 seed：规则 / 命中 / 情景推演 / 生命周期（JSON 组 dynamic，mock 原样落库）。

- rule ← RULE_DEFS（16）；confirmer ← RULE_CONFIRMER；
- rule_version ← derived/ruleVersions.json（28）；rule_draft ← RULE_DRAFTS（R-007 → CR-0011，
  [推断] 单号取 releases.ts DRAFT_TARGETS，master 还没有变更单表）；
- rule_hit ← derived/hitsOf.json（1186，alert 命中 result=alert、alert_id 真外键）；
- scenario ← SCENARIOS（18）；scenario_result / scenario_impact ← SCENARIOS.result（15 / 341）；
- lifecycle ← LIFECYCLES（6）；lifecycle_version ← LIFECYCLES.versions（14），
  snapshot ← derived/lifecycleAsOf；
- lifecycle_state / lifecycle_transition ← 当前版本的 states / transitions（只给当前版本建行，
  历史版本的状态机在 lifecycle_version.snapshot 里 [推断]）；
- lifecycle_change ← derived/changesOf.json（246），trId → 当前版本迁移。
"""

from typing import Any

from app.core.schema import parse_display_dt
from app.modules.action.models import ActionType
from app.modules.dynamic.models import Alert, AlertLevel
from app.modules.dynamic.rules_models import (
    HitResult,
    LcStateKind,
    LcTrigger,
    Lifecycle,
    LifecycleChange,
    LifecycleState,
    LifecycleTransition,
    LifecycleVersion,
    Rule,
    RuleDraft,
    RuleHit,
    RuleOutput,
    RuleStatus,
    RuleVersion,
    Scenario,
    ScenarioImpact,
    ScenarioResult,
    ScenarioStatus,
)
from app.modules.event.models import EventType
from app.modules.static.models import ObjectType
from app.modules.versioning.models import OntologyRelease
from app.seeds import Ref, Row, SeedContext, SeedSpec, derived_args

SEED_RULES = 16
SEED_RULE_VERSIONS = 28
SEED_HITS = 1186
SEED_SCENARIOS = 18
SEED_SCENARIO_RESULTS = 15
SEED_IMPACTS = 341
SEED_LIFECYCLES = 6
SEED_LC_VERSIONS = 14
SEED_LC_CHANGES = 246

# [推断] releases.ts DRAFT_TARGETS：R-007 在 CR-0011
RULE_DRAFT_CR = {"R-007": "CR-0011"}
DRAFT_BY, DRAFT_AT = "何静", "2026-10-24 18:40"  # ruleVersions(rule, draftVer) 的草稿行


def lc_ver_code(obj: str, ver: int) -> str:
    return f"{obj}:v{ver}"


def lc_state_code(obj: str, ver: int, key: str) -> str:
    return f"{obj}:v{ver}:{key}"


def _confirmers(ctx: SeedContext) -> dict[str, str]:
    return ctx.load("RULE_CONFIRMER.json")


def _rule(r: Row, ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "version": r["version"],
        "name": r["name"],
        "desc": r["desc"],
        "owner": r["owner"],
        "status": RuleStatus(r["status"]).name,
        "core": bool(r.get("core")),
        "obj_kind": r["objKind"],
        "level": AlertLevel(r["level"]).name,
        "output": RuleOutput(r["output"]).name,
        "conds": r["conds"],
        "logic": r["logic"],
        "expr": r["expr"],
        "dedupe": r["dedupe"],
        "cooldown": r["cooldown"],
        "trigger": r["trigger"],
        "action_type_id": r.get("actionTypeId"),
        "auto_stage": r["autoStage"],
        "weekly": r.get("weekly", 0),
        "confirmer": _confirmers(ctx).get(r["id"]),
        "release_id": None,
    }


def _rule_version(r: Row, _ctx: SeedContext) -> Row:
    return {
        "rule_id": derived_args(r)[0],
        "version": r["ver"],
        "by": r["by"],
        "at": parse_display_dt(r["at"]),
        "change": r["change"],
    }


def _rule_draft(r: Row, ctx: SeedContext) -> Row | None:
    patch = ctx.load("RULE_DRAFTS.json").get(r["id"])
    if not patch:
        return None
    return {
        "rule_id": r["id"],
        "cr_code": RULE_DRAFT_CR[r["id"]],
        "version": patch["version"],
        "patch": {k: v for k, v in patch.items() if k != "version"},
        "by": DRAFT_BY,
        "at": parse_display_dt(DRAFT_AT),
        "change": _draft_change(r["id"], patch["version"], ctx),
    }


def _draft_change(rule_id: str, ver: int, ctx: SeedContext) -> str:
    for x in ctx.load("derived/ruleVersions.draft.json"):
        if x["args"][0]["id"] == rule_id and x["args"][1] == ver:
            return next(v["change"] for v in x["result"] if v["status"] == "草稿")
    return ""


def _hit(r: Row, ctx: SeedContext) -> Row:
    rule_id = derived_args(r)[0]
    seqs = ctx.cache.setdefault("__dyn_hit_seq", {})
    seqs[rule_id] = seqs.get(rule_id, 0) + 1
    res = r["result"]
    result = HitResult(res).name if res in (HitResult.notified.value, HitResult.deduped.value) else "alert"
    return {
        "code": r["id"],
        "rule_id": rule_id,
        "rule_version": None,
        "seq": seqs[rule_id],
        "at": parse_display_dt(r["at"]),
        "obj_kind": r["objKind"],
        "obj_id": r["objId"],
        "obj_label": r["objLabel"],
        "store_id": r.get("storeId"),
        "store_label": r.get("storeLabel"),
        "value": r["value"],
        "threshold": r["threshold"],
        "dedupe_key": None,
        "result": result,
        "alert_id": r.get("alertId"),
    }


def _scenario(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "owner": r["owner"],
        "created_on": parse_display_dt(r["createdAt"]),
        "base_release_id": r.get("baseVer"),
        "status": ScenarioStatus(r["status"]).name,
        "scope": r["scope"],
        "drivers": r["drivers"],
        "fail_reason": r.get("failReason"),
        "staged": r.get("staged") or [],
    }


def _scenario_result(r: Row, _ctx: SeedContext) -> Row | None:
    res = r.get("result")
    if not res:
        return None
    return {
        "scenario_id": r["id"],
        "ran_at": parse_display_dt(res["ranAt"]),
        "scope": r["scope"],
        "drivers": r["drivers"],
        "weeks": res["weeks"],
        "base_sales": res["baseSales"],
        "sales": res["sales"],
        "base_margin": res["baseMargin"],
        "margin": res["margin"],
        "kpis": res["kpis"],
    }


def _impacts(r: Row, _ctx: SeedContext) -> list[Row]:
    res = r.get("result") or {}
    return [
        {
            "scenario_id": r["id"],
            "seq": i + 1,
            "sku": x["sku"],
            "name": x["name"],
            "category": x["category"],
            "store_id": x["storeId"],
            "store_name": x["storeName"],
            "base_sales": x["baseSales"],
            "sales": x["sales"],
            "stockout_base": x["stockoutBase"],
            "stockout": x["stockout"],
            "suggest": x["suggest"],
        }
        for i, x in enumerate(res.get("impacts", []))
    ]


def _lifecycle(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["objectId"],
        "object_type_id": r["objectId"],
        "object_name": r["objectName"],
        "version": r["version"],
        "field": r["field"],
        "owner": r["owner"],
        "updated_on": parse_display_dt(r["updatedAt"]),
        "rule_code": r.get("ruleId"),
        "desc": r["desc"],
    }


def _as_of(ctx: SeedContext) -> dict[tuple[str, str], dict[str, Any]]:
    key = "__dyn_lc_asof"
    if key not in ctx.cache:
        ctx.cache[key] = {
            (c["args"][0], c["args"][1]): c["result"] for c in ctx.load("derived/lifecycleAsOf.json")
        }
    return ctx.cache[key]


def snapshot_of(lc: dict[str, Any]) -> dict[str, Any]:
    return {"states": lc["states"], "transitions": lc["transitions"]}


def _lc_versions(r: Row, ctx: SeedContext) -> list[Row]:
    out = []
    for v in r["versions"]:
        snap = _as_of(ctx).get((r["objectId"], v["at"]))
        out.append(
            {
                "code": lc_ver_code(r["objectId"], v["ver"]),
                "lifecycle_id": r["objectId"],
                "version": v["ver"],
                "at": parse_display_dt(v["at"]),
                "by": v["by"],
                "change": v["change"],
                "undo": v.get("undo"),
                "release_id": v.get("release"),
                "snapshot": snapshot_of(snap) if snap else None,
            }
        )
    return out


def _lc_states(r: Row, _ctx: SeedContext) -> list[Row]:
    obj, ver = r["objectId"], r["version"]
    return [
        {
            "code": lc_state_code(obj, ver, s["key"]),
            "version_id": lc_ver_code(obj, ver),
            "seq": i + 1,
            "key": s["key"],
            "name": s["name"],
            "kind": LcStateKind(s["kind"]).name,
            "desc": s.get("desc", ""),
            "sla": s.get("sla"),
            "count": s.get("count", 0),
            "x": s["x"],
            "y": s["y"],
        }
        for i, s in enumerate(r["states"])
    ]


def _lc_transitions(r: Row, _ctx: SeedContext) -> list[Row]:
    obj, ver = r["objectId"], r["version"]
    return [
        {
            "code": lc_state_code(obj, ver, t["id"]),
            "version_id": lc_ver_code(obj, ver),
            "seq": i + 1,
            "tr_code": t["id"],
            "from_state_id": lc_state_code(obj, ver, t["from"]),
            "to_state_id": lc_state_code(obj, ver, t["to"]),
            "trigger": LcTrigger(t["trigger"]).name,
            "by": t["by"],
            "event_type_id": t.get("eventId"),
            "action_type_id": t.get("actionId"),
            "guard": t.get("guard", ""),
            "effect": t.get("effect", ""),
            "weekly": t.get("weekly", 0),
        }
        for i, t in enumerate(r["transitions"])
    ]


def _lc_cur_ver(ctx: SeedContext) -> dict[str, int]:
    return {x["objectId"]: x["version"] for x in ctx.load("LIFECYCLES.json")}


def _lc_change(r: Row, ctx: SeedContext) -> Row:
    obj = derived_args(r)[0]
    tr = r.get("trId")
    return {
        "code": r["id"],
        "lifecycle_id": obj,
        "at": parse_display_dt(r["at"]),
        "obj_id": r["objId"],
        "obj_label": r["objLabel"],
        "from_state": r["from"],
        "to_state": r["to"],
        "transition_id": lc_state_code(obj, _lc_cur_ver(ctx)[obj], tr) if tr else None,
        "by": r["by"],
        "alert_id": r.get("alertId"),
    }


def check_rule_counts(by_table: dict[str, list[Row]]) -> None:
    want = {
        Rule: SEED_RULES,
        RuleVersion: SEED_RULE_VERSIONS,
        RuleHit: SEED_HITS,
        Scenario: SEED_SCENARIOS,
        ScenarioResult: SEED_SCENARIO_RESULTS,
        ScenarioImpact: SEED_IMPACTS,
        Lifecycle: SEED_LIFECYCLES,
        LifecycleVersion: SEED_LC_VERSIONS,
        LifecycleChange: SEED_LC_CHANGES,
    }
    errors = [
        f"{m.__tablename__} {len(by_table.get(m.__tablename__, []))} != {n}"
        for m, n in want.items()
        if len(by_table.get(m.__tablename__, [])) != n
    ]
    if errors:
        raise ValueError("dynamic 规则 / 推演 / 生命周期 seed 行数不对：" + "；".join(errors))


_RULE = Ref(Rule, nullable=False)
_LC = Ref(Lifecycle, nullable=False)
_LCV = Ref(LifecycleVersion, nullable=False)
_LCS = Ref(LifecycleState, nullable=False)
_SC = Ref(Scenario, nullable=False)

RULE_CHECKS = [check_rule_counts]

RULE_SEEDS: list[SeedSpec] = [
    SeedSpec(
        Rule,
        "RULE_DEFS.json",
        key=("code",),
        transform=_rule,
        refs={"action_type_id": Ref(ActionType)},
        description="规则",
    ),
    SeedSpec(
        RuleVersion,
        "derived/ruleVersions.json",
        key=("rule_id", "version"),
        transform=_rule_version,
        refs={"rule_id": _RULE},
        description="规则版本",
    ),
    SeedSpec(
        RuleDraft,
        "RULE_DEFS.json",
        key=("rule_id", "cr_code"),
        transform=_rule_draft,
        refs={"rule_id": _RULE},
        description="规则草稿（RULE_DRAFTS）",
    ),
    SeedSpec(
        RuleHit,
        "derived/hitsOf.json",
        key=("code",),
        transform=_hit,
        refs={"rule_id": _RULE, "alert_id": Ref(Alert)},
        description="规则命中",
    ),
    SeedSpec(
        Scenario,
        "SCENARIOS.json",
        key=("code",),
        transform=_scenario,
        refs={"base_release_id": Ref(OntologyRelease, key="ver")},
        description="情景推演方案",
    ),
    SeedSpec(
        ScenarioResult,
        "SCENARIOS.json",
        key=("scenario_id",),
        transform=_scenario_result,
        refs={"scenario_id": _SC},
        description="推演结果",
    ),
    SeedSpec(
        ScenarioImpact,
        "SCENARIOS.json",
        key=("scenario_id", "sku", "store_id"),
        transform=_impacts,
        refs={"scenario_id": _SC},
        description="推演影响明细",
    ),
    SeedSpec(
        Lifecycle,
        "LIFECYCLES.json",
        key=("code",),
        transform=_lifecycle,
        refs={"object_type_id": Ref(ObjectType, nullable=False)},
        description="生命周期",
    ),
    SeedSpec(
        LifecycleVersion,
        "LIFECYCLES.json",
        key=("code",),
        transform=_lc_versions,
        refs={"lifecycle_id": _LC, "release_id": Ref(OntologyRelease, key="ver")},
        description="生命周期版本",
    ),
    SeedSpec(
        LifecycleState,
        "LIFECYCLES.json",
        key=("code",),
        transform=_lc_states,
        refs={"version_id": _LCV},
        description="生命周期状态（当前版本）",
    ),
    SeedSpec(
        LifecycleTransition,
        "LIFECYCLES.json",
        key=("code",),
        transform=_lc_transitions,
        refs={
            "version_id": _LCV,
            "from_state_id": _LCS,
            "to_state_id": _LCS,
            "event_type_id": Ref(EventType),
            "action_type_id": Ref(ActionType),
        },
        description="生命周期迁移（当前版本）",
    ),
    SeedSpec(
        LifecycleChange,
        "derived/changesOf.json",
        key=("code",),
        transform=_lc_change,
        refs={"lifecycle_id": _LC, "transition_id": Ref(LifecycleTransition), "alert_id": Ref(Alert)},
        description="状态变化记录",
    ),
]
