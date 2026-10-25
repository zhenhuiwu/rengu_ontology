"""dynamic 模块 seed（JSON 组 dynamic；RUNS 在 action 组）。前端 mock 原样落库，不改任何取值。

写入顺序（loader 按列表顺序写）：
1. alert ← dynamic/ALERTS.json（154）；rule_version 取自时间线第一行「规则 X vN 命中」；
2. action_run ← dynamic/ACTIONS.json（141，in_center=true）；
3. action_run ← action/derived/runsOf.json（1849，in_center=false）；共 1990；
4. action_run_log ← ACTIONS 的 log；
5. action_run_log ← action/derived/ACTION_EVENT_RESOLVERS.action.json 的 timeline
   （只有首行带日期，fill_timeline_dates 补全）；
6. alert_log ← dynamic/derived/alertTimeline.json 450 行 + alertActionLink 的发起行 5 行，按时间稳定排序；
7. alert.action_run_id ← ALERTS 第二遍 + alertActionLink（action_run 写入之后才能解析 id）；
8. sys_user.alert_assignable ← dynamic/PEOPLE.json 顺序 1..7，按姓名找用户；找不到报错，不新建用户。

[推断] 派生说明：
- RUNS 没有 sysId / typeVersion：取 action/ACTION_TYPES.json 里该动作类型的 sysId / version；
- RUNS 的已对账行 reconcile 是一句说明文字：原文进 reconcile_note；code 全是「对账确认：外部已处理」，
  reconcile_result 记 succeeded；
- RUNS 里 494 行没有 objKind（告警操作、会员标签），保持 null；
- 告警日志 op / from_status / to_status 按日志文本推出（接口不输出）。
"""

import re
from typing import Any

from app.core.enums import TimelineStatus
from app.core.schema import parse_display_dt
from app.modules.admin.models import SysUser
from app.modules.admin.seeds import code_by_name
from app.modules.dynamic.models import (
    ActionRun,
    ActionRunLog,
    ActionStatus,
    Alert,
    AlertLevel,
    AlertLog,
    AlertOp,
    AlertStatus,
    ReconcileResult,
    SourceType,
)
from app.seeds import Ref, Row, SeedContext, SeedSpec, derived_args, fill_timeline_dates

JSON_MODULE = "dynamic"

SEED_ALERTS = 154
SEED_ALERT_LOGS = 455
SEED_ACTIONS = 141
SEED_RUNS = 1849
SEED_DUP_IDEM_KEYS = 31

_RULE_LINE = re.compile(r"^规则 (\S+) (v\d+) 命中")


def _action_types(ctx: SeedContext) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in ctx.load("ACTION_TYPES.json", group="action")}


def _timeline_by_alert(ctx: SeedContext) -> dict[str, list[dict[str, Any]]]:
    key = "__dyn_timeline"
    if key not in ctx.cache:
        ctx.cache[key] = {
            str(c["args"][0]["id"]): c["result"] for c in ctx.load("derived/alertTimeline.json")
        }
    return ctx.cache[key]


def _links(ctx: SeedContext) -> dict[str, dict[str, Any]]:
    key = "__dyn_links"
    if key not in ctx.cache:
        calls = ctx.load("derived/alertActionLink.json")
        ctx.cache[key] = {c["result"]["alert_id"]: c["result"] for c in calls}
    return ctx.cache[key]


def _alert(r: Row, ctx: SeedContext) -> Row:
    first = (_timeline_by_alert(ctx).get(r["id"]) or [{}])[0].get("text", "")
    m = _RULE_LINE.match(first)
    return {
        "code": r["id"],
        "rule_code": r["ruleId"],
        "rule_version": m.group(2) if m and m.group(1) == r["ruleId"] else None,
        "level": AlertLevel(r["level"]).name,
        "obj_kind": r["objKind"],
        "obj_id": r["objId"],
        "obj_label": r["objLabel"],
        "store_id": r.get("storeId"),
        "store_label": r.get("storeLabel"),
        "value": r["value"],
        "threshold": r["threshold"],
        "triggered_at": parse_display_dt(r["triggeredAt"]),
        "data_cutoff": parse_display_dt(r["cutoff"]),
        "status": AlertStatus(r["status"]).name,
        "assignee": r.get("assignee"),
    }


def _alert_link(r: Row, ctx: SeedContext) -> Row:
    link = _links(ctx).get(r["id"]) or {}
    return {**_alert(r, ctx), "action_run_id": link.get("action_run_id")}


def _action(r: Row, ctx: SeedContext) -> Row:
    types = _action_types(ctx)
    t = next(x for x in types.values() if x["name"] == r["action"])
    src = r["source"]
    return {
        "code": r["id"],
        "action_type_code": t["id"],
        "action_name": r["action"],
        "type_version": r.get("typeVersion"),
        "source_type": SourceType(src["type"]).name,
        "source_label": src["type"],
        "rule_code": src.get("ruleId"),
        "rule_version": src.get("ruleVersion"),
        "alert_id": src.get("alertId"),
        "qa_session": src.get("session"),
        "qa_session_title": src.get("sessionTitle"),
        "obj_kind": r.get("objKind"),
        "obj_id": r.get("objId"),
        "obj_label": r.get("objLabel"),
        "params_text": r.get("params"),
        "elapsed_ms": None,
        "result_code": None,
        "retry_count": None,
        "initiator": r["initiator"],
        "confirmer": src.get("confirmer"),
        "approver": r.get("approver"),
        "idem_key": r["idemKey"],
        "status": ActionStatus(r["status"]).name,
        "sys_code": r.get("sysId"),
        "ext_ref": r.get("extRef"),
        "fail_reason": r.get("failReason"),
        "unknown_reason": r.get("unknownReason"),
        "reconcile_result": ReconcileResult(r["reconcile"]).name if r.get("reconcile") else None,
        "reconcile_note": None,
        "submitted_at": parse_display_dt(r["submittedAt"]),
        "in_center": True,
    }


def _run(r: Row, ctx: SeedContext) -> Row:
    t = _action_types(ctx)[r["typeId"]]
    reconciled = r["status"] == ActionStatus.reconciled.value
    return {
        "code": r["id"],
        "action_type_code": r["typeId"],
        "action_name": t["name"],
        "type_version": t.get("version"),  # [推断] RUNS 没有 typeVersion
        "source_type": SourceType(r["source"]).name,
        "source_label": r["source"],
        "rule_code": r.get("ruleId"),
        "rule_version": None,
        "alert_id": r.get("alertId"),
        "qa_session": None,
        "qa_session_title": None,
        "obj_kind": r.get("objKind"),
        "obj_id": r.get("objId"),
        "obj_label": r.get("objLabel"),
        "params_text": r.get("summary"),
        "elapsed_ms": r.get("ms"),
        "result_code": r.get("code"),
        "retry_count": r.get("retries"),
        "initiator": r["initiator"],
        "confirmer": r.get("confirmer"),
        "approver": r.get("approver"),
        "idem_key": r["idemKey"],
        "status": ActionStatus(r["status"]).name,
        "sys_code": t.get("sysId"),  # [推断] RUNS 没有 sysId
        "ext_ref": r.get("extRef"),
        "fail_reason": None,
        "unknown_reason": None,
        "reconcile_result": ReconcileResult.succeeded.name if reconciled else None,
        "reconcile_note": r.get("reconcile"),
        "submitted_at": parse_display_dt(r["at"]),
        "in_center": False,
    }


def _runs(r: Row, ctx: SeedContext) -> list[Row]:
    """runsOf 每个动作类型一行（result = 该类型的执行记录数组），loader 已按数组展开成单条。"""
    return [_run(r, ctx)]


def _log_rows(code: str, lines: list[dict[str, Any]]) -> list[Row]:
    times = fill_timeline_dates(lines)
    return [
        {
            "action_run_id": code,
            "seq": i,
            "at": at,
            "text": ln["text"],
            "actor": ln.get("desc"),
            "level": TimelineStatus(ln["status"]).name if ln.get("status") else None,
        }
        for i, (ln, at) in enumerate(zip(lines, times, strict=True), start=1)
    ]


def _action_log(r: Row, _ctx: SeedContext) -> list[Row]:
    return _log_rows(r["id"], r.get("log") or [])


def _run_log(r: Row, _ctx: SeedContext) -> list[Row]:
    return _log_rows(str(derived_args(r)[0]), r.get("timeline") or [])


def _alert_op(text: str) -> tuple[str, str | None, str | None]:
    """日志文本 → (op, from_status, to_status)。[推断]"""
    if "生成告警" in text:
        return AlertOp.created.name, None, AlertStatus.open.name
    if text.startswith("认领"):
        return AlertOp.claim.name, AlertStatus.open.name, AlertStatus.processing.name
    if text.startswith("转派"):
        return AlertOp.transfer.name, None, AlertStatus.processing.name
    if text.startswith("关联动作"):
        return AlertOp.action_result.name, AlertStatus.processing.name, AlertStatus.resolved.name
    if text.startswith("关闭为"):
        to = AlertStatus(text[3:6]).name
        return AlertOp.close.name, AlertStatus.processing.name, to
    if text.startswith("发起动作"):
        return AlertOp.start_action.name, None, None
    return AlertOp.note.name, None, None


def _alert_log(r: Row, ctx: SeedContext) -> list[Row]:
    code = str(derived_args(r)[0])
    lines = list(_timeline_by_alert(ctx).get(code) or [])
    link = _links(ctx).get(code)
    if link and link.get("log_line"):
        lines.append(link["log_line"])
    times = fill_timeline_dates(lines)
    ordered = sorted(zip(lines, times, strict=True), key=lambda x: x[1])  # 稳定排序
    out = []
    for i, (ln, at) in enumerate(ordered, start=1):
        op, frm, to = _alert_op(ln["text"])
        out.append(
            {
                "alert_id": code,
                "seq": i,
                "at": at,
                "actor": ln["who"],
                "op": op,
                "text": ln["text"],
                "from_status": frm,
                "to_status": to,
                "abnormal": bool(ln.get("abnormal")),
            }
        )
    return out


def _alert_timeline(r: Row, ctx: SeedContext) -> list[Row] | None:
    """alertTimeline 的派生结果是数组，loader 会逐行展开；这里只在每个告警的第一行整体生成。"""
    code = str(derived_args(r)[0])
    seen = ctx.cache.setdefault("__dyn_alert_log_done", set())
    if code in seen:
        return None
    seen.add(code)
    return _alert_log(r, ctx)


def _assignable(_r: Row, ctx: SeedContext) -> list[Row] | None:
    """sys_user.alert_assignable：PEOPLE（姓名数组）顺序 1..7。

    loader 只能读对象数组，PEOPLE 是字符串数组，所以挂在 ALERTS.json 上、只在第一行生成全部 7 行。
    id 列经 Ref 换成已有用户的 id：用户不存在直接报错（只更新、不新建用户）。
    """
    if ctx.cache.get("__dyn_people_done"):
        return None
    ctx.cache["__dyn_people_done"] = True
    rows = []
    for i, name in enumerate(ctx.load("PEOPLE.json"), start=1):
        code = code_by_name(name)  # 姓名找不到 / 重名直接报错
        rows.append({"id": code, "code": code, "name": name, "alert_assignable": i})
    return rows


class _ExistingUser:
    """Ref 目标：已有的 sys_user（按 code 查 id）。

    不能直接写 Ref(SysUser)：loader 会把「表引用自己」当成自引用分层写入。这里只用来把 code 换成 id，
    找不到就报错，保证这一步只更新已有用户、不新建用户。
    """

    __table__ = SysUser.__table__
    code = SysUser.code
    id = SysUser.id


def check_counts(by_table: dict[str, list[Row]]) -> None:
    alerts = {r["code"] for r in by_table.get(Alert.__tablename__, [])}
    runs = by_table.get(ActionRun.__tablename__, [])
    codes = [r["code"] for r in runs]
    errors = []
    if len(alerts) != SEED_ALERTS:
        errors.append(f"alert {len(alerts)} != {SEED_ALERTS}")
    if len(codes) != SEED_ACTIONS + SEED_RUNS or len(set(codes)) != len(codes):
        want = SEED_ACTIONS + SEED_RUNS
        errors.append(f"action_run {len(codes)} 行 / {len(set(codes))} 个编号，应为 {want}")
    dup = len(codes) - len({r["idem_key"] for r in runs})
    if dup != SEED_DUP_IDEM_KEYS:
        errors.append(f"重复幂等键 {dup} != {SEED_DUP_IDEM_KEYS}")
    if len(by_table.get(AlertLog.__tablename__, [])) != SEED_ALERT_LOGS:
        errors.append(f"alert_log != {SEED_ALERT_LOGS}")
    missing = {r["alert_id"] for r in runs if r["alert_id"]} - alerts
    if missing:
        errors.append(f"动作引用了不存在的告警：{sorted(missing)[:5]}")
    if errors:
        raise ValueError("dynamic seed 校验失败：" + "；".join(errors))


CHECKS = [check_counts]

SEEDS: list[SeedSpec] = [
    SeedSpec(Alert, "ALERTS.json", key=("code",), transform=_alert, description="告警"),
    SeedSpec(
        ActionRun,
        "ACTIONS.json",
        key=("code",),
        transform=_action,
        refs={"alert_id": Ref(Alert)},
        description="动作中心 ACTIONS",
    ),
    SeedSpec(
        ActionRun,
        "derived/runsOf.json",
        key=("code",),
        transform=_runs,
        group="action",
        refs={"alert_id": Ref(Alert)},
        description="动作类型执行记录 RUNS",
    ),
    SeedSpec(
        ActionRunLog,
        "ACTIONS.json",
        key=("action_run_id", "seq"),
        transform=_action_log,
        refs={"action_run_id": Ref(ActionRun, nullable=False)},
        description="ACTIONS 时间线",
    ),
    SeedSpec(
        ActionRunLog,
        "derived/ACTION_EVENT_RESOLVERS.action.json",
        key=("action_run_id", "seq"),
        transform=_run_log,
        group="action",
        refs={"action_run_id": Ref(ActionRun, nullable=False)},
        description="RUNS 执行时间线",
    ),
    SeedSpec(
        AlertLog,
        "derived/alertTimeline.json",
        key=("alert_id", "seq"),
        transform=_alert_timeline,
        refs={"alert_id": Ref(Alert, nullable=False)},
        description="告警处理日志",
    ),
    SeedSpec(
        Alert,
        "ALERTS.json",
        key=("code",),
        transform=_alert_link,
        refs={"action_run_id": Ref(ActionRun)},
        description="告警关联动作（action_run 写入后回填）",
    ),
    SeedSpec(
        SysUser,
        "ALERTS.json",
        key=("code",),
        transform=_assignable,
        refs={"id": Ref(_ExistingUser, nullable=False)},  # type: ignore[arg-type]
        description="sys_user.alert_assignable ← PEOPLE 顺序",
    ),
]
