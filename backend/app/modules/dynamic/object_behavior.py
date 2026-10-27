"""对象行为页的轻量目录、实际计数和命中SQL分页。"""

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, raiseload

from app.common.pagination import Page
from app.core import clock
from app.core.errors import NotFound
from app.modules.action.models import ActionType, ActionTypeRule
from app.modules.action.service import with_system
from app.modules.dynamic.models import ActionRun, ActionStatus, Alert
from app.modules.dynamic.repo import recent_rule_hits
from app.modules.dynamic.row_scope import scoped_instances
from app.modules.dynamic.rules_models import HitResult, Rule, RuleHit
from app.modules.dynamic.rules_service import RuleService
from app.modules.integration.models import ExtSystem
from app.modules.static.models import ObjectType
from app.modules.versioning.models import OntologyRelease


async def object_head(session, code, ver=None):
    if "\x00" in code or len(code) > 64:
        raise NotFound("对象类型不存在")
    if ver:
        version = (
            await session.execute(
                select(OntologyRelease.snapshot["objectTypes"][code]).where(OntologyRelease.ver == ver)
            )
        ).first()
        if version is None:
            raise NotFound("版本不存在")
        data = version[0]
        if not isinstance(data, dict):
            raise NotFound("对象类型在该版本不存在")
        return SimpleNamespace(code=code, api_name=data.get("api"), display_name=data.get("name"))
    row = (
        await session.execute(
            select(ObjectType.code, ObjectType.api_name, ObjectType.display_name).where(
                ObjectType.code == code
            )
        )
    ).first()
    if row is None:
        raise NotFound("对象类型不存在")
    return row


async def behavior_stats(session, code, ctx, user):
    obj = await object_head(session, code, ctx.ver)
    until = clock.now()
    rule_rows = list(await session.execute(select(Rule.id, Rule.code).where(Rule.obj_kind == code)))
    rule_ids = {row.id for row in rule_rows}
    weekly = await recent_rule_hits(session, user=user, until=until, rule_ids=rule_ids)
    alert_counts = (
        dict(
            (
                await session.execute(
                    await scoped_instances(
                        select(RuleHit.rule_id, func.count())
                        .where(
                            RuleHit.rule_id.in_(rule_ids),
                            RuleHit.at >= until - timedelta(days=7),
                            RuleHit.at <= until,
                            RuleHit.result == HitResult.alert,
                        )
                        .group_by(RuleHit.rule_id),
                        user, RuleHit.obj_kind, RuleHit.obj_id,
                    )
                )
            ).all()
        )
        if rule_ids
        else {}
    )
    pending = (
        dict(
            (
                await session.execute(
                    await scoped_instances(
                        select(ActionRun.rule_code, func.count())
                        .where(
                            ActionRun.rule_code.in_({r.code for r in rule_rows}),
                            ActionRun.status == ActionStatus.staged,
                        )
                        .group_by(ActionRun.rule_code),
                        user, ActionRun.obj_kind, ActionRun.obj_id,
                    )
                )
            ).all()
        )
        if rule_rows
        else {}
    )
    center = await session.execute(
        await scoped_instances(
            select(ActionRun.status, func.count())
            .where(ActionRun.obj_kind == code, ActionRun.in_center.is_(True))
            .group_by(ActionRun.status),
            user, ActionRun.obj_kind, ActionRun.obj_id,
        )
    )
    types = []
    if not ctx.ver:
        types = list(
            await session.scalars(
                select(ActionType)
                .options(raiseload("*"), joinedload(ActionType.system))
                .where(
                    ActionType.retired_release_id.is_(None),
                    or_(
                        ActionType.target_kind == code,
                        ActionType.target_kind.is_(None) & (ActionType.target_api == obj.api_name),
                    ),
                )
                .order_by(ActionType.id)
            )
        )
    type_ids = {row.id for row in types}
    used_by = {}
    if type_ids:
        for parent, rule in await session.execute(
            select(ActionTypeRule.action_type_id, ActionTypeRule.rule_code)
            .where(ActionTypeRule.action_type_id.in_(type_ids))
            .order_by(ActionTypeRule.seq, ActionTypeRule.id)
        ):
            used_by.setdefault(parent, []).append(rule)
    actions = []
    for row in types:
        head = {
            "id": row.code,
            "name": row.name,
            "api": row.api,
            "group": row.grp,
            "status": row.status.value,
            "version": row.version,
            "target": row.target,
            "targetApi": row.target_api,
            "targetKind": row.target_kind,
            "owner": row.owner,
            "desc": row.desc_tpl,
            "runners": row.runners,
            "approval": row.approval,
            "sysId": row.system.code if row.system else None,
            "endpoint": row.endpoint,
            "idemKey": row.biz_key_template,
            "usedBy": used_by.get(row.id, []),
        }
        actions.append(with_system(head, {row.system.code: row.system} if row.system else {}))
    if ctx.ver:
        snapshot = await session.scalar(
            select(OntologyRelease.snapshot["actionTypes"]).where(OntologyRelease.ver == ctx.ver)
        )
        systems = {row.code: row for row in await session.scalars(select(ExtSystem))}
        for definition in (snapshot or {}).values():
            if definition.get("targetKind") == code or (
                not definition.get("targetKind") and definition.get("targetApi") == obj.api_name
            ):
                actions.append(
                    with_system(
                        {
                            key: value
                            for key, value in definition.items()
                            if key not in ("params", "conds", "writeback")
                        },
                        systems,
                    )
                )
    executions = await session.execute(
        await scoped_instances(
            select(ActionRun.action_type_code, ActionRun.status, func.count())
            .where(
                ActionRun.action_type_code.in_({row["id"] for row in actions}),
                ActionRun.submitted_at >= until - timedelta(days=30),
                ActionRun.submitted_at <= until,
                ActionRun.status.in_(
                    (
                        ActionStatus.succeeded, ActionStatus.reconciled,
                        ActionStatus.failed, ActionStatus.unknown,
                    )
                ),
            )
            .group_by(ActionRun.action_type_code, ActionRun.status),
            user, ActionRun.obj_kind, ActionRun.obj_id,
        )
    )
    counts = {}
    for type_code, status, count in executions:
        counts.setdefault(type_code, {})[status] = count
    by_type = {}
    for row in actions:
        total = sum(counts.get(row["id"], {}).values())
        count = counts.get(row["id"], {})
        ok = count.get(ActionStatus.succeeded, 0) + count.get(ActionStatus.reconciled, 0)
        by_type[row["id"]] = {
            "total": total,
            "rate": ok / total if total else 0,
            "failed": count.get(ActionStatus.failed, 0),
            "unknown": count.get(ActionStatus.unknown, 0),
        }
    return {
        "rules": {
            r.code: {
                "hits7d": weekly.get(r.id, 0),
                "alerts7d": alert_counts.get(r.id, 0),
                "pending": pending.get(r.code, 0),
            }
            for r in rule_rows
        },
        "centerStatusCounts": {status.value: count for status, count in center},
        "actions30d": by_type,
        "actions": actions,
    }


async def automation_hits(session, code, rule_id, page, user):
    await object_head(session, code)
    until = clock.now()
    statement = (
        select(RuleHit, Rule.code.label("rule_code"), Rule.name.label("rule_name"))
        .join(Rule, Rule.id == RuleHit.rule_id)
        .where(Rule.obj_kind == code, RuleHit.at >= until - timedelta(days=7), RuleHit.at <= until)
    )
    if rule_id:
        statement = statement.where(Rule.code == rule_id)
    statement = await scoped_instances(statement, user, RuleHit.obj_kind, RuleHit.obj_id)
    total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    p = page.clamp(total)
    rows = list(
        await session.execute(
            statement.order_by(RuleHit.at.desc(), RuleHit.id.desc()).limit(p.size).offset(p.offset)
        )
    )
    ids = {row.RuleHit.alert_id for row in rows if row.RuleHit.alert_id is not None}
    alerts = (
        {alert.id: alert for alert in await session.scalars(select(Alert).where(Alert.id.in_(ids)))}
        if ids
        else {}
    )
    return Page.of(
        [
            {
                **RuleService.hit_out(row.RuleHit, row.rule_code, alerts.get(row.RuleHit.alert_id)),
                "ruleName": row.rule_name,
            }
            for row in rows
        ],
        total,
        page,
    )
