"""Frozen rule provenance and one-target, read-only checks before an external claim."""
from datetime import datetime, timedelta
from sqlalchemy import select
from app.common.auth import check_func
from app.modules.action.condition_expr import Unavailable
from app.core import clock
from app.core.errors import AppError
from app.modules.dynamic.models import Alert, SourceType
from app.modules.dynamic.rules_models import HitResult, Rule, RuleHit, RuleScheduleRun, RuleStatus
from app.modules.event.models import Event


def rejected(code, message):
    return AppError(message, code=code, status_code=409)


def freeze_origin(run, rule, alert, source):
    ttl = (rule.auto_stage or {}).get("validitySeconds", 0)
    if type(ttl) is not int or not 0 <= ttl <= 86400:
        raise rejected("RULE_ACTION_ORIGIN_INVALID", "规则动作有效期无效，未排队执行")
    return {**source, "ruleId": rule.code, "ruleVersion": rule.version, "hitCode": alert.hit_code,
            "target": {"kind": run.obj_kind, "id": run.obj_id},
            "expiresAt": (run.submitted_at + timedelta(seconds=ttl)).isoformat() if ttl else None}


async def validate_rule_dispatch(session, user, run):
    origin = (run.review_snapshot if isinstance(run.review_snapshot, dict) else {}).get("origin")
    if origin is None and run.source_type != SourceType.rule:
        return
    if not isinstance(origin, dict):
        raise rejected("RULE_ACTION_ORIGIN_MISSING", "原规则动作缺少可信来源，需人工核对后重新发起")
    if (origin.get("target") != {"kind": run.obj_kind, "id": run.obj_id} or origin.get("ruleId") != run.rule_code
        or type(origin.get("ruleVersion")) is not int or not isinstance(origin.get("hitCode"), str)):
        raise rejected("RULE_ACTION_ORIGIN_INVALID", "规则来源与动作目标不一致，未发送外部请求")
    expiry, deadline = origin.get("expiresAt"), None
    if expiry is not None:
        try:
            deadline = datetime.fromisoformat(expiry)
            if deadline.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError):
            raise rejected("RULE_ACTION_ORIGIN_INVALID", "规则动作有效截止时间无效") from None
        if clock.now() >= deadline:
            raise rejected("RULE_ACTION_EXPIRED", "规则动作已超过配置有效期，未发送外部请求；请重新核对并发起")
    check_func(user, "rules", "manage")
    rule = await session.scalar(select(Rule).where(Rule.code == run.rule_code))
    if rule is None or rule.status == RuleStatus.disabled or rule.version != origin.get("ruleVersion") or str(run.rule_version).removeprefix("v") != str(rule.version):
        raise rejected("RULE_ACTION_SOURCE_CHANGED", "来源规则已停用或版本变化，未发送外部请求")
    trigger = rule.trigger or {}
    stage = rule.auto_stage or {}
    if not trigger.get("enabled", trigger.get("kind") == "事件") or not stage.get("enabled") or stage.get("actionTypeId") != run.action_type_code:
        raise rejected("RULE_ACTION_SOURCE_CHANGED", "来源规则触发已关闭，未发送外部请求")
    alert = await session.scalar(select(Alert).where(Alert.id == run.alert_id))
    hit = await session.scalar(select(RuleHit).where(RuleHit.code == origin.get("hitCode")))
    if (alert is None or hit is None or alert.action_run_id != run.id or hit.alert_id != alert.id
        or alert.rule_code != rule.code or alert.obj_kind != run.obj_kind or alert.obj_id != run.obj_id
        or alert.hit_code != hit.code or hit.rule_id != rule.id or hit.rule_version != rule.version
        or hit.result != HitResult.alert or hit.obj_kind != run.obj_kind or hit.obj_id != run.obj_id):
        raise rejected("RULE_ACTION_ORIGIN_INVALID", "原命中记录与当前动作不一致，未发送外部请求")
    from app.modules.dynamic.sections import rule_def
    definition, event, snapshot = rule_def(rule, None), None, None
    if origin.get("kind") == "event":
        check_func(user, "event-types", "manage")
        event = await session.scalar(select(Event).where(Event.code == origin.get("eventCode")))
        if (event is None or event.is_seed or event.is_test or event.obj_kind != run.obj_kind or event.object_id != run.obj_id or not isinstance(event.definition_snapshot, dict) or not isinstance(event.payload, dict)
            or trigger.get("kind") != "事件" or trigger.get("eventId") != (event.definition_snapshot or {}).get("id")):
            raise rejected("RULE_ACTION_ORIGIN_INVALID", "原事件与规则目标不一致，未发送外部请求")
        from app.modules.versioning.models import OntologyRelease
        release = await session.get(OntologyRelease, event.release_id)
        if release is None:
            raise rejected("RULE_ACTION_ORIGIN_INVALID", "原事件发布快照不可用")
        snapshot = release.snapshot
        bound = (snapshot or {}).get("rules", {}).get(rule.code) or {}
        if bound.get("version") != rule.version or bound.get("expr") != rule.expr:
            raise rejected("RULE_ACTION_SOURCE_CHANGED", "原事件绑定规则与当前规则不一致")
    elif origin.get("kind") == "schedule":
        source = await session.get(RuleScheduleRun, origin.get("scheduleRunId"))
        if (source is None or source.rule_id != rule.id or source.rule_version != rule.version or trigger.get("kind") != "定时"
            or (source.definition or {}).get("expr") != rule.expr):
            raise rejected("RULE_ACTION_ORIGIN_INVALID", "原定时运行与规则版本不一致")
    else:
        raise rejected("RULE_ACTION_ORIGIN_MISSING", "原规则动作来源不完整，需人工重新发起")
    from app.modules.dynamic.rule_runtime import evaluate_target
    try:
        matched = await evaluate_target(session, user, definition, run.obj_id, event=event, snapshot=snapshot)
    except Unavailable:
        raise rejected("RULE_ACTION_RECHECK_UNAVAILABLE", "原规则条件当前无法核验，未发送外部请求") from None
    if not matched:
        raise rejected("RULE_ACTION_PREDICATE_CHANGED", "原规则条件当前已不满足，未发送外部请求")
    return deadline
