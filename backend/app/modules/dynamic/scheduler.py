"""Opt-in scheduled rules, durable slots and atomic local alert creation."""

import hashlib
import json
import logging
from datetime import UTC, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import func, select

from app.common.auth import check_func, resolve_user
from app.core.clock import now
from app.core.errors import AppError
from app.db.dialects import get_adapter
from app.jobs.scope import scoped_sessions, workspace_ids
from app.modules.dynamic.event_rule_runtime import dedupe_spec
from app.modules.dynamic.models import Alert, AlertLog, AlertOp, AlertStatus
from app.modules.dynamic.rule_editor import validate_trigger
from app.modules.dynamic.rule_runtime import run_rule, unavailable
from app.modules.dynamic.rules_models import (
    HitResult,
    Rule,
    RuleHit,
    RuleOutput,
    RuleScheduleCursor,
    RuleScheduleRun,
    RuleStatus,
)
from app.modules.static.models import ObjectType
from app.modules.versioning.repo import ReleaseRepo

logger = logging.getLogger(__name__)


def fingerprint(user):
    identity = [user.id, user.func, user.roles, user.row_filters, user.attrs]
    if user.ontology_id != 1:
        identity.insert(0, user.ontology_id)
    return hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()


def next_slot(trigger, after):
    validate_trigger(trigger)
    return (
        croniter(trigger["cron"], after.astimezone(ZoneInfo(trigger.get("timezone", "Asia/Shanghai"))))
        .get_next(type(after))
        .astimezone(UTC)
    )


def slot_expired(trigger, slot, at):
    minutes = trigger.get("catchupMinutes", 0)
    delay = at - slot
    return delay > timedelta(minutes=minutes) if minutes else delay >= timedelta(minutes=1)


async def reset_cursor(session, rule, at):
    """Called only by explicit CR publication; loading seed definitions never arms timers."""
    cursor = await session.scalar(
        select(RuleScheduleCursor).where(RuleScheduleCursor.rule_id == rule.id).with_for_update()
    )
    trigger = rule.trigger or {}
    if (
        trigger.get("enabled") is not True
        or trigger.get("kind") != "定时"
        or rule.status == RuleStatus.disabled
    ):
        if cursor:
            await session.delete(cursor)
        return
    due = next_slot(trigger, at)
    if cursor:
        cursor.rule_version, cursor.next_at = rule.version, due
    else:
        session.add(
            RuleScheduleCursor(
                rule_id=rule.id, rule_version=rule.version, next_at=due, created_by=trigger["runAsUserId"]
            )
        )


async def claim(session, cursor_id):
    # shortcut: publication lock serializes rule effects; use shared locks if rule throughput grows.
    await get_adapter(session).xact_lock(session, "ontology.publish")
    cursor = await session.scalar(
        select(RuleScheduleCursor).where(RuleScheduleCursor.id == cursor_id).with_for_update(skip_locked=True)
    )
    if cursor is None or cursor.next_at > now():
        return
    rule = await session.get(Rule, cursor.rule_id)
    trigger = rule.trigger or {}
    if (
        rule.version != cursor.rule_version
        or rule.status == RuleStatus.disabled
        or trigger.get("enabled") is not True
    ):
        await session.delete(cursor)
        return
    release = await ReleaseRepo(session).latest()
    definition = ((release.snapshot or {}).get("rules") or {}).get(rule.code) if release else None
    if not definition or definition.get("version") != rule.version or definition.get("trigger") != trigger:
        # A rollback must not silently re-arm a previous schedule.
        await session.delete(cursor)
        return
    actor_id = trigger.get("runAsUserId")
    try:
        actor = await resolve_user(session, actor_id)
        scope = fingerprint(actor)
    except AppError:
        scope = ""
    at, slot = now(), cursor.next_at
    # Compensate at most the latest eligible slot; never reconstruct historical object states.
    latest = (
        croniter(
            trigger["cron"],
            (at + timedelta(microseconds=1)).astimezone(ZoneInfo(trigger.get("timezone", "Asia/Shanghai"))),
        )
        .get_prev(type(at))
        .astimezone(UTC)
    )
    if latest >= slot:
        slot = latest
    expired = slot_expired(trigger, slot, at)
    session.add(
        RuleScheduleRun(
            rule_id=rule.id,
            rule_version=rule.version,
            release_id=release.id,
            scheduled_at=slot,
            state="skipped" if expired else "pending",
            definition=definition,
            permission_fingerprint=scope,
            created_by=actor_id,
            finished_at=at if expired else None,
            error="已超过补偿范围；未还原历史对象状态" if expired else None,
            result={"coalescedFrom": cursor.next_at.isoformat(), "evaluationMode": "current"},
        )
    )
    cursor.next_at = next_slot(trigger, at)


async def create_alerts(session, user, rule, run, matches):
    if rule.output != RuleOutput.alert:
        raise unavailable("通知规则尚无真实通知通道，未标记已通知")
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == rule.obj_kind))
    at, counts = now(), {"alerts": 0, "deduped": 0, "actions": 0}
    local = at.astimezone(ZoneInfo(rule.trigger.get("timezone", "Asia/Shanghai")))
    fields = [
        SimpleNamespace(api="date", name="日期"),
        SimpleNamespace(api="week", name="周"),
        SimpleNamespace(api="hour", name="小时"),
    ]
    seq = int(await session.scalar(select(func.max(RuleHit.seq)).where(RuleHit.rule_id == rule.id)) or 0)
    for item in matches:
        identity = hashlib.sha256(
            f"schedule\0{run.ontology_id}\0{rule.id}\0{run.rule_version}\0{run.scheduled_at.isoformat()}\0{item['objId']}".encode()
        ).hexdigest()[:60]
        event = SimpleNamespace(
            code=f"schedule:{run.id}",
            object_id=item["objId"],
            definition_snapshot={"object": obj.display_name},
            payload={
                "date": str(local.date()),
                "week": str(local.date() - timedelta(days=local.weekday())),
                "hour": local.strftime("%Y-%m-%dT%H%z"),
            },
        )
        key, window = dedupe_spec(rule, event, fields)
        recent = select(RuleHit).where(
            RuleHit.rule_id == rule.id,
            RuleHit.dedupe_key == key,
            RuleHit.result == HitResult.alert,
            RuleHit.is_seed.is_(False),
        )
        if window is not None:
            recent = recent.where(RuleHit.at > at - window)
        duplicate = await session.scalar(recent.order_by(RuleHit.at.desc()).limit(1))
        seq += 1
        hit = RuleHit(
            code=f"RH-{identity}",
            rule_id=rule.id,
            rule_version=rule.version,
            seq=seq,
            at=at,
            obj_kind=rule.obj_kind,
            obj_id=item["objId"],
            obj_label=item["objLabel"],
            dims=[],
            value=item["value"][:200],
            threshold=rule.expr[:200],
            dedupe_key=key,
            result=HitResult.deduped if duplicate else HitResult.alert,
            created_by=user.id,
        )
        if duplicate:
            hit.alert_id = duplicate.alert_id
            session.add(hit)
            counts["deduped"] += 1
            continue
        alert = Alert(
            code=f"AL-{identity}",
            rule_code=rule.code,
            rule_version=f"v{rule.version}",
            hit_code=hit.code,
            dedupe_key=key,
            level=rule.level,
            obj_kind=rule.obj_kind,
            obj_id=item["objId"],
            obj_label=item["objLabel"],
            dims=[],
            value=item["value"][:200],
            threshold=rule.expr[:200],
            triggered_at=at,
            data_cutoff=at,
            status=AlertStatus.open,
            created_by=user.id,
        )
        session.add(alert)
        await session.flush()
        hit.alert_id = alert.id
        session.add(hit)
        session.add(
            AlertLog(
                alert_id=alert.id,
                seq=1,
                at=at,
                actor=user.id,
                op=AlertOp.created,
                text=f"定时规则 {rule.code} v{rule.version} · 计划 {run.scheduled_at.isoformat()}",
                created_by=user.id,
            )
        )
        counts["alerts"] += 1
        if (rule.auto_stage or {}).get("enabled"):
            from app.modules.dynamic.event_rule_runtime import stage_action

            context = SimpleNamespace(obj_kind=rule.obj_kind, object_id=item["objId"],
                                      obj_label=item["objLabel"], payload={})
            await stage_action(session, user, rule, context, [], alert, item["value"])
            counts["actions"] += 1
    return counts


async def process(session, run_id):
    await get_adapter(session).xact_lock(session, "ontology.publish")
    run = await session.scalar(
        select(RuleScheduleRun)
        .where(RuleScheduleRun.id == run_id, RuleScheduleRun.state == "pending")
        .with_for_update(skip_locked=True)
    )
    if run is None:
        return
    run.started_at = now()
    expired = slot_expired(run.definition.get("trigger") or {}, run.scheduled_at, now())
    newer = await session.scalar(
        select(RuleScheduleRun.id)
        .where(
            RuleScheduleRun.rule_id == run.rule_id,
            RuleScheduleRun.rule_version == run.rule_version,
            RuleScheduleRun.scheduled_at > run.scheduled_at,
        )
        .limit(1)
    )
    if expired or newer:
        run.state, run.finished_at = "skipped", now()
        run.error = "已超过补偿范围" if expired else "已有较新计划，本次已合并"
        return
    try:
        async with session.begin_nested():
            user = await resolve_user(session, run.created_by)
            for page in ("rules", "alerts"):
                check_func(user, page, "manage")
            if fingerprint(user) != run.permission_fingerprint:
                raise unavailable("运行身份权限已变化，请重新发布启用配置")
            rule = await session.scalar(select(Rule).where(Rule.id == run.rule_id).with_for_update())
            if (
                rule.version != run.rule_version
                or rule.status == RuleStatus.disabled
                or rule.trigger != run.definition.get("trigger")
            ):
                raise unavailable("规则版本或启用状态已变化，已取消本次运行")
            release = await ReleaseRepo(session).latest()
            if (
                not release
                or release.id != run.release_id
                or ((release.snapshot or {}).get("rules") or {}).get(rule.code) != run.definition
            ):
                raise unavailable("已发布规则定义发生变化，已取消本次运行")
            body = SimpleNamespace(mode="snapshot", range="30d", anchor=None, page=1, size=10000)
            result = await run_rule(session, user, run.definition, run.definition, body, internal=True)
            matches = result.pop("_matches")
            counts = await create_alerts(session, user, rule, run, matches)
            run.result = {
                **(run.result or {}),
                **{key: result[key] for key in ("ranAt", "candidates", "evaluated", "unknown", "would")},
            }
            run.result = {
                **run.result,
                **counts,
                "actionStatus": "processed" if (rule.auto_stage or {}).get("enabled") else "not_configured",
            }
            run.state = "partial" if result["unknown"] else "done"
    except AppError as exc:
        run.state, run.error = "failed", str(exc)
    run.finished_at = now()


async def sweep(timestamp: int = 0) -> int:
    total = 0
    for ontology_id in await workspace_ids():
        from app.common.ontology import WorkspaceInactive
        try:
            total += await _sweep_workspace(ontology_id)
        except WorkspaceInactive:
            continue
    return total


async def _sweep_workspace(ontology_id: int) -> int:
    sessions = scoped_sessions(ontology_id)
    async with sessions() as session, session.begin():
        ids = list(
            await session.scalars(
                select(RuleScheduleCursor.id)
                .where(RuleScheduleCursor.next_at <= now())
                .order_by(RuleScheduleCursor.next_at)
                .limit(100)
            )
        )
    for cursor_id in ids:
        try:
            async with sessions() as session, session.begin():
                await claim(session, cursor_id)
        except Exception:
            logger.exception("Rule schedule claim failed: %s", cursor_id)
    async with sessions() as session, session.begin():
        pending = list(
            await session.scalars(
                select(RuleScheduleRun.id)
                .where(RuleScheduleRun.state == "pending")
                .order_by(RuleScheduleRun.id)
                .limit(100)
            )
        )
    for run_id in pending:
        try:
            async with sessions() as session, session.begin():
                await process(session, run_id)
        except Exception:
            logger.exception("Rule schedule transaction failed: %s", run_id)
            async with sessions() as session, session.begin():
                row = await session.scalar(
                    select(RuleScheduleRun)
                    .where(RuleScheduleRun.id == run_id, RuleScheduleRun.state == "pending")
                    .with_for_update()
                )
                if row:
                    row.state, row.error, row.finished_at = (
                        "failed",
                        "运行事务失败；未保存部分业务结果",
                        now(),
                    )
    try:
        # 告警事务都已提交：为新告警补自动决策建议（尽力而为，失败只记日志）
        from app.modules.dynamic.auto_decision import decide_pending

        await decide_pending(ontology_id)
    except Exception:
        logger.exception("Auto decision sweep failed: ontology %s", ontology_id)
    return len(pending)
